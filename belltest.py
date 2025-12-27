import pygame
import time

# Initialiseer de mixer
pygame.mixer.init()

# Laad en speel het geluid af
pygame.mixer.music.load("/home/pi/schoolbell/static/geluiden/1 ivko schoolbel.mp3")  # Zorg dat dit bestand in dezelfde map staat
pygame.mixer.music.play()

# Wacht tot het geluid klaar is
while pygame.mixer.music.get_busy():
    time.sleep(0.1)
