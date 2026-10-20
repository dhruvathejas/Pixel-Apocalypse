import pygame
    
def init(gfps, gname):    
    fps = int(gfps)
    clock = pygame.time.Clock()
    screen = pygame.display.set_mode((1920, 1000))
    pygame.display.set_caption(str(gname))

class asset:
    class img:
        def load(img, type):
            return pygame.image.load(str(img) + "." + type)
