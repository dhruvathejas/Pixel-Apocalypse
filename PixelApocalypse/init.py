import gdev
import menu

gdev.init(60, "PixelApocalypse")
pygame = gdev.pygame

map = gdev.asset.img.load("PixelApocalypse")

running = True
while running:
    for events in pygame.event.get():
        if events.type == pygame.QUIT:
                running = False
                exit()
        
        elif pygame.KEYDOWN:
            if events.type == pygame.KEYDOWN:
                 if events.key == pygame.K_ESCAPE:
                    running = False
                    exit()

    if menu.game_start == True:
         gdev.screen.blit(map, (0, 0))
    
    gdev.clock.tick(gdev.fps)
    pygame.display.flip()
