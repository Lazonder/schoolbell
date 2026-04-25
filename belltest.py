import pygame
import time

# Initialize the mixer
pygame.mixer.init()

# Load and play the sound
pygame.mixer.music.load("/home/pi/schoolbell/static/geluiden/1 ivko schoolbel.mp3")  # Make sure this file lives in the same folder
pygame.mixer.music.play()

# Wait for the sound to finish
while pygame.mixer.music.get_busy():
    time.sleep(0.1)
