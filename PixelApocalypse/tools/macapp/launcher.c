/* Pixel Apocalypse.app launcher: Contents/MacOS/Pixel Apocalypse.
 *
 * Built by tools/make_app.py (clang -arch x86_64 -mmacosx-version-min=10.13 -O2).
 *
 * The game is plain Python 3.9 + the vendored pygame (an x86_64-only wheel), so the app
 * does not ship a Python. This launcher loads Apple's own Python 3.9 framework (from the
 * Command Line Tools or Xcode) into its own process and runs <Resources>/game/init.py with
 * it, so macOS sees one real app (Dock icon, name, menu bar) instead of a python3 process.
 *
 * x86_64 only on purpose: on Apple Silicon the whole app then runs under Rosetta 2, the
 * universal Python framework loads its x86_64 slice and the x86_64 pygame .so files load.
 *
 * Python 3.9 first came with the Command Line Tools / Xcode 14 (macOS 12.5+); older tools
 * have 3.7 or 3.8, which the cp39 pygame cannot use, so they count as "too old".
 *
 * Environment for the game:
 *   PIXEL_APOCALYPSE_DATA_DIR   ~/Library/Application Support/Pixel Apocalypse (created);
 *                               an already-set value is kept (tests point it elsewhere) but
 *                               made absolute ("~/" expanded) before the game sees it
 *   python -E -s -B             ignore every PYTHON* variable of the user's shell, ignore
 *                               ~/Library/Python/3.9 packages, never write __pycache__ into
 *                               the (signed) bundle. The framework finds its stdlib from its
 *                               own location. (PYTHONDONTWRITEBYTECODE / PYTHONNOUSERSITE are
 *                               set too, and PYTHONHOME / PYTHONPATH removed, for child processes.)
 *
 * Test hooks:
 *   PIXEL_APOCALYPSE_PYTHON_LIB      use exactly this Python3 dylib (a missing file = "not found")
 *   PIXEL_APOCALYPSE_DEVELOPER_DIR   look only in this developer dir (no xcode-select, no fallbacks)
 *   PIXEL_APOCALYPSE_NO_ALERT=1      skip the modal dialogs (stderr + exit code only)
 */
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pwd.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

#define APP_NAME "Pixel Apocalypse"
#define PY_LIB_REL "/Library/Frameworks/Python3.framework/Versions/3.9/Python3"
#define EXIT_NO_PYTHON 3
#define EXIT_BROKEN_APP 4

typedef int (*py_bytes_main_fn)(int argc, char **argv);

/* One line on stderr (Console.app shows it for Finder launches). */
__attribute__((format(printf, 1, 2)))
static void log_line(const char *fmt, ...)
{
    va_list ap;
    fprintf(stderr, "[" APP_NAME "] ");
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

static int is_file(const char *p)
{
    struct stat st;
    return p && p[0] && stat(p, &st) == 0 && S_ISREG(st.st_mode);
}

static int is_dir(const char *p)
{
    struct stat st;
    return p && p[0] && stat(p, &st) == 0 && S_ISDIR(st.st_mode);
}

static void strip_newline(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == '/'))
        s[--n] = '\0';
}

/* ---------------------------------------------------------------- finding Python 3.9 */

/* The active developer dir: "xcode-select -p", else the symlink it maintains. */
static int developer_dir(char *out, size_t size)
{
    FILE *f = popen("/usr/bin/xcode-select -p 2>/dev/null", "r");
    if (f) {
        char line[PATH_MAX];
        int ok = fgets(line, sizeof line, f) != NULL;
        int status = pclose(f);
        if (ok && status == 0) {
            strip_newline(line);
            if (line[0]) {
                snprintf(out, size, "%s", line);
                return 1;
            }
        }
    }
    {
        char link[PATH_MAX];
        ssize_t n = readlink("/var/db/xcode_select_link", link, sizeof link - 1);
        if (n > 0) {
            link[n] = '\0';
            strip_newline(link);
            snprintf(out, size, "%s", link);
            return 1;
        }
    }
    return 0;
}

/* dlopen() the first usable Python 3.9 framework. Returns the handle (path in lib_out) or NULL;
   then *tools_found says whether some developer tools are installed (just without Python 3.9). */
static void *load_python(char *lib_out, size_t size, int *tools_found)
{
    char devs[3][PATH_MAX], candidates[4][PATH_MAX];
    int ndev = 0, count = 0, i;
    const char *override = getenv("PIXEL_APOCALYPSE_PYTHON_LIB");
    const char *dev_override = getenv("PIXEL_APOCALYPSE_DEVELOPER_DIR");

    *tools_found = 0;
    if (override && override[0]) {
        snprintf(candidates[count++], PATH_MAX, "%s", override);   /* testing: only this one */
    } else {
        if (dev_override && dev_override[0]) {
            snprintf(devs[ndev++], PATH_MAX, "%s", dev_override);   /* testing: only this one */
        } else {
            if (developer_dir(devs[ndev], PATH_MAX))
                ndev++;
            snprintf(devs[ndev++], PATH_MAX, "/Library/Developer/CommandLineTools");
            snprintf(devs[ndev++], PATH_MAX, "/Applications/Xcode.app/Contents/Developer");
        }
        for (i = 0; i < ndev; i++) {
            char bin[PATH_MAX];
            snprintf(bin, sizeof bin, "%s/usr/bin", devs[i]);
            if (is_dir(bin))
                *tools_found = 1;
            snprintf(candidates[count++], PATH_MAX, "%s" PY_LIB_REL, devs[i]);
        }
    }

    for (i = 0; i < count; i++) {
        void *h;
        if (!is_file(candidates[i]))
            continue;
        h = dlopen(candidates[i], RTLD_NOW | RTLD_GLOBAL);
        if (h) {
            snprintf(lib_out, size, "%s", candidates[i]);
            return h;
        }
        log_line("could not load %s", dlerror());
    }
    return NULL;
}

/* ---------------------------------------------------------------- alerts */

/* Run a command, wait for it; 1 if it exited 0. */
static int run_tool(char **args)
{
    pid_t pid;
    int status;
    if (posix_spawn(&pid, args[0], NULL, NULL, args, environ) != 0)
        return 0;
    if (waitpid(pid, &status, 0) != pid)
        return 0;
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

static void open_software_update(void)
{
    char *args[] = {"/usr/bin/open", "x-apple.systempreferences:com.apple.preferences.softwareupdate", NULL};
    if (!run_tool(args))
        log_line("could not open Software Update");
}

static void run_xcode_select_install(void)
{
    char *args[] = {"/usr/bin/xcode-select", "--install", NULL};
    /* Fails with "already installed" when old tools are there: send them to Software Update. */
    if (!run_tool(args))
        open_software_update();
}

/* Native dialog (CoreFoundation, no AppKit needed). Returns 1 if the default button was chosen. */
static int alert(const char *title, const char *message, const char *ok, const char *other)
{
    CFOptionFlags response = kCFUserNotificationCancelResponse;
    CFStringRef t, m, b1, b2 = NULL;
    const char *no_alert = getenv("PIXEL_APOCALYPSE_NO_ALERT");

    if (no_alert && no_alert[0] && strcmp(no_alert, "0") != 0)
        return 0;
    t = CFStringCreateWithCString(NULL, title, kCFStringEncodingUTF8);
    m = CFStringCreateWithCString(NULL, message, kCFStringEncodingUTF8);
    b1 = CFStringCreateWithCString(NULL, ok, kCFStringEncodingUTF8);
    if (other)
        b2 = CFStringCreateWithCString(NULL, other, kCFStringEncodingUTF8);
    CFUserNotificationDisplayAlert(0, kCFUserNotificationStopAlertLevel, NULL, NULL, NULL,
                                   t, m, b1, b2, NULL, &response);
    CFRelease(t);
    CFRelease(m);
    CFRelease(b1);
    if (b2)
        CFRelease(b2);
    return (response & 3) == kCFUserNotificationDefaultResponse;
}

static int no_python(void)
{
    log_line("Python 3.9 not found: install Apple's Command Line Tools (xcode-select --install)");
    if (alert(APP_NAME " needs Apple's Command Line Tools",
              "Pixel Apocalypse runs on the Python 3.9 that comes with Apple's Command Line "
              "Tools for Xcode, and it is not installed on this Mac.\n\n"
              "Click Install to download them from Apple (free, a few minutes), "
              "then open Pixel Apocalypse again.",
              "Install", "Quit"))
        run_xcode_select_install();
    return EXIT_NO_PYTHON;
}

static int python_too_old(void)
{
    log_line("Python 3.9 not found: these Command Line Tools are too old, update to version 14 "
             "or newer (macOS 12.5+) in Software Update");
    if (alert(APP_NAME " needs newer Command Line Tools",
              "Pixel Apocalypse runs on the Python 3.9 that comes with Apple's Command Line "
              "Tools 14 or newer (macOS 12.5 or later). The Command Line Tools on this Mac are "
              "an older version without it.\n\n"
              "Update them in Software Update, then open Pixel Apocalypse again.",
              "Software Update", "Quit"))
        open_software_update();
    return EXIT_NO_PYTHON;
}

/* ---------------------------------------------------------------- paths */

/* Contents/Resources of this bundle, from the real (symlink-free) executable path. */
static int resources_dir(char *out, size_t size)
{
    char raw[PATH_MAX], real[PATH_MAX], *slash;
    uint32_t n = sizeof raw;
    int i;

    if (_NSGetExecutablePath(raw, &n) != 0 || !realpath(raw, real))
        return 0;
    for (i = 0; i < 2; i++) {            /* .../Contents/MacOS/Pixel Apocalypse -> .../Contents */
        slash = strrchr(real, '/');
        if (!slash || slash == real)
            return 0;
        *slash = '\0';
    }
    snprintf(out, size, "%s/Resources", real);
    return 1;
}

/* mkdir -p */
static int make_dirs(const char *p)
{
    char buf[PATH_MAX], *s;
    snprintf(buf, sizeof buf, "%s", p);
    for (s = buf + 1; *s; s++) {
        if (*s == '/') {
            *s = '\0';
            if (mkdir(buf, 0755) != 0 && errno != EEXIST)
                return 0;
            *s = '/';
        }
    }
    return mkdir(buf, 0755) == 0 || errno == EEXIST;
}

static const char *home_dir(void)
{
    const char *home = getenv("HOME");
    if (!home || !home[0]) {
        struct passwd *pw = getpwuid(getuid());
        home = pw ? pw->pw_dir : "/tmp";
    }
    return home;
}

static void setup_data_dir(void)
{
    const char *set = getenv("PIXEL_APOCALYPSE_DATA_DIR");
    char dir[PATH_MAX], real[PATH_MAX];

    if (set && set[0] == '~' && (set[1] == '/' || set[1] == '\0'))
        snprintf(dir, sizeof dir, "%s%s", home_dir(), set + 1);   /* a quoted "~/..." */
    else if (set && set[0])
        snprintf(dir, sizeof dir, "%s", set);
    else
        snprintf(dir, sizeof dir, "%s/Library/Application Support/" APP_NAME, home_dir());
    if (!make_dirs(dir))
        log_line("could not create the save folder %s", dir);
    /* Absolute before the chdir below, so the game (gdev.DATA_DIR) uses this same folder. */
    if (realpath(dir, real))
        snprintf(dir, sizeof dir, "%s", real);
    setenv("PIXEL_APOCALYPSE_DATA_DIR", dir, 1);
    /* Started from Finder the working folder is "/": use the save folder instead, so any
       stray relative write lands there and never inside the signed bundle. */
    if (chdir(dir) != 0)
        log_line("could not enter %s", dir);
}

/* ---------------------------------------------------------------- main */

int main(int argc, char **argv)
{
    char res[PATH_MAX], script[PATH_MAX], lib[PATH_MAX];
    char **py_argv;
    int py_argc = 0, i, tools_found;
    void *handle;
    py_bytes_main_fn py_bytes_main;

    if (!resources_dir(res, sizeof res)) {
        log_line("cannot locate the app bundle");
        return EXIT_BROKEN_APP;
    }
    snprintf(script, sizeof script, "%s/game/init.py", res);
    if (!is_file(script)) {
        log_line("game files missing: %s", script);
        alert(APP_NAME " is damaged", "The game files inside the app are missing. "
              "Download Pixel Apocalypse again.", "Quit", NULL);
        return EXIT_BROKEN_APP;
    }

    handle = load_python(lib, sizeof lib, &tools_found);
    if (!handle)
        return tools_found ? python_too_old() : no_python();
    py_bytes_main = (py_bytes_main_fn)dlsym(handle, "Py_BytesMain");
    if (!py_bytes_main) {
        log_line("%s has no Py_BytesMain (not Python 3.8+)", lib);
        return no_python();
    }

    setup_data_dir();
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONSTARTUP");
    unsetenv("__PYVENV_LAUNCHER__");    /* set by /usr/bin/python3; would change sys.executable */

    /* argv: [argv0, -E, -s, -B, init.py, user args...] minus Finder's old "-psn_0_12345"
       argument. Py_BytesMain reads the options exactly like python3's command line. */
    py_argv = calloc((size_t)argc + 5, sizeof(char *));
    if (!py_argv)
        return EXIT_BROKEN_APP;
    py_argv[py_argc++] = argv[0];
    py_argv[py_argc++] = "-E";
    py_argv[py_argc++] = "-s";
    py_argv[py_argc++] = "-B";
    py_argv[py_argc++] = script;
    for (i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0)
            continue;
        py_argv[py_argc++] = argv[i];
    }
    py_argv[py_argc] = NULL;
    return py_bytes_main(py_argc, py_argv);
}
