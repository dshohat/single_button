from machine import Pin, I2C, PWM
import ssd1306
import time
import random
import math
import framebuf

# --- Hardware Setup ---
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=400000)
display = ssd1306.SSD1306_I2C(128, 64, i2c)
button = Pin(4, Pin.IN, Pin.PULL_UP)
buzzer_pwm = PWM(Pin(23))

def buzz(duration_ms, frequency):
    if frequency > 0:
        buzzer_pwm.freq(frequency)
        buzzer_pwm.duty(512)
    time.sleep_ms(duration_ms)
    buzzer_pwm.duty(0)

def play_startup_music():
    """Play a catchy little startup jingle."""
    # Simple ascending melody (like a retro game boot)
    melody = [
        (150, 523),  # C5
        (150, 587),  # D5
        (150, 659),  # E5
        (150, 784),  # G5
        (100, 0),    # pause
        (150, 659),  # E5
        (300, 784),  # G5 (held)
        (100, 0),    # pause
        (150, 880),  # A5
        (400, 1047), # C6 (held)
    ]
    for duration, freq in melody:
        buzz(duration, freq)
        time.sleep_ms(30)  # tiny gap between notes

def play_victory_music():
    """Play a triumphant win jingle."""
    melody = [
        (120, 523),  # C5
        (120, 659),  # E5
        (120, 784),  # G5
        (300, 1047), # C6
        (100, 0),
        (120, 784),  # G5
        (400, 1047), # C6
        (100, 0),
        (150, 1175), # D6
        (500, 1319), # E6
    ]
    for duration, freq in melody:
        buzz(duration, freq)
        time.sleep_ms(30)

def fireworks(display):
    """Draw fireworks animation on the OLED display."""
    for burst in range(5):
        display.fill(0)
        # Random center for each burst
        cx = random.randint(20, 108)
        cy = random.randint(10, 40)
        # Draw expanding "explosion" rings
        for radius in range(2, 18, 3):
            display.fill(0)
            display.text("YOU WIN!", 32, 0)
            # Draw radial lines (star burst pattern)
            for angle_step in range(12):
                angle = angle_step * 3.14159 / 6  # 12 spokes
                x1 = int(cx + (radius - 2) * math.cos(angle))
                y1 = int(cy + (radius - 2) * math.sin(angle))
                x2 = int(cx + radius * math.cos(angle))
                y2 = int(cy + radius * math.sin(angle))
                display.pixel(x2, y2, 1)
                if radius > 5:
                    display.pixel(x1, y1, 1)
            # Draw some scattered sparks
            for _ in range(8):
                sx = cx + random.randint(-radius, radius)
                sy = cy + random.randint(-radius, radius)
                if 0 <= sx < 128 and 0 <= sy < 64:
                    display.pixel(sx, sy, 1)
            display.show()
            buzz(20, random.randint(800, 2000))  # crackle sound
            time.sleep_ms(60)
        # Flash the whole burst
        display.fill(0)
        display.text("YOU WIN!", 32, 0)
        display.fill_rect(cx - 10, cy - 10, 20, 20, 1)
        display.show()
        buzz(30, 1500)
        time.sleep_ms(100)
    # Final screen
    display.fill(0)
    display.text("YOU WIN!", 32, 20)
    display.show()

# --- Game Constants ---
SCREEN_WIDTH = 128
SCREEN_HEIGHT = 64
GROUND_Y = 55
DINO_X = 10
DINO_WIDTH = 16
DINO_HEIGHT = 16

# --- Dino Sprite (16x16) ---
# A small T-Rex looking right: head, eye, arms, body, legs
DINO_BITMAP = bytearray([
    0b00000000, 0b01111100,  # row 0        .....XXXXX..
    0b00000000, 0b11111110,  # row 1       .XXXXXXX.
    0b00000000, 0b11011110,  # row 2       .XX.XXXX.
    0b00000000, 0b11111110,  # row 3       .XXXXXXX.
    0b00000000, 0b11111110,  # row 4       .XXXXXXX.
    0b00000000, 0b11100000,  # row 5       .XXX.....
    0b00000001, 0b11111100,  # row 6      XXXXXXXX..
    0b00000011, 0b11111000,  # row 7     XXXXXXX...
    0b01000111, 0b11110000,  # row 8   . XXXXXXXX..
    0b01001111, 0b11110000,  # row 9   . .XXXXXXXX.
    0b11111111, 0b11100000,  # row 10  XXXXXXXXXX..
    0b11111111, 0b11100000,  # row 11  XXXXXXXXXX..
    0b00111111, 0b11000000,  # row 12   .XXXXXXXX..
    0b00011111, 0b10000000,  # row 13    .XXXXXXX..
    0b00001101, 0b10000000,  # row 14     XX.XX...
    0b00001100, 0b01000000,  # row 15     XX...X..
])
DINO_FB = framebuf.FrameBuffer(DINO_BITMAP, 16, 16, framebuf.MONO_HLSB)

# --- Tree Sprite (10x16) ---
TREE_BITMAP = bytearray([
    0b00001100, 0b00000000,  # row 0       ..XX......
    0b00011110, 0b00000000,  # row 1      .XXXX.....
    0b00111111, 0b00000000,  # row 2     .XXXXXX...
    0b01111111, 0b10000000,  # row 3    .XXXXXXXX.
    0b11111111, 0b11000000,  # row 4   XXXXXXXXXX
    0b01111111, 0b10000000,  # row 5    XXXXXXXX.
    0b00111111, 0b00000000,  # row 6     .XXXXXX..
    0b01111111, 0b10000000,  # row 7    XXXXXXXX.
    0b11111111, 0b11000000,  # row 8   XXXXXXXXXX
    0b11111111, 0b11000000,  # row 9   XXXXXXXXXX
    0b01111111, 0b10000000,  # row 10   XXXXXXXX.
    0b00111111, 0b00000000,  # row 11    XXXXXX..
    0b00001100, 0b00000000,  # row 12      XX......
    0b00001100, 0b00000000,  # row 13      XX......
    0b00001100, 0b00000000,  # row 14      XX......
    0b00001100, 0b00000000,  # row 15      XX......
])
TREE_FB = framebuf.FrameBuffer(TREE_BITMAP, 16, 16, framebuf.MONO_HLSB)

# --- Rock Sprite (12x8) padded to 16x8 ---
ROCK_BITMAP = bytearray([
    0b00000110, 0b00000000,  # row 0        .XX.........
    0b00011111, 0b10000000,  # row 1     .XXXXXXX....
    0b00111111, 0b11000000,  # row 2    .XXXXXXXXX..
    0b01111111, 0b11100000,  # row 3   .XXXXXXXXXXX
    0b11111111, 0b11110000,  # row 4  XXXXXXXXXXXX.
    0b11111111, 0b11110000,  # row 5  XXXXXXXXXXXX.
    0b01111111, 0b11100000,  # row 6   XXXXXXXXXXX.
    0b00111111, 0b11000000,  # row 7    XXXXXXXXX..
])
ROCK_FB = framebuf.FrameBuffer(ROCK_BITMAP, 16, 8, framebuf.MONO_HLSB)

ROCK_WIDTH = 12
ROCK_HEIGHT = 8
TREE_WIDTH = 10
TREE_HEIGHT = 16

# Obstacle state
obstacle_type = "tree"  # or "rock"

# --- Game Variables ---
dino_y = GROUND_Y - DINO_HEIGHT
velocity_y = 0
gravity = 2
jump_strength = -10
is_jumping = False

cactus_x = 128
cactus_width = TREE_WIDTH
cactus_height = TREE_HEIGHT
score = 0
game_speed = 5
lives = 3
WIN_SCORE = 10  # Reach this score to win!

def reset_game():
    global dino_y, velocity_y, is_jumping, cactus_x, score, game_speed, obstacle_type, cactus_width, cactus_height, lives
    dino_y = GROUND_Y - DINO_HEIGHT
    velocity_y = 0
    is_jumping = False
    cactus_x = 128
    score = 0
    game_speed = 5
    lives = 3
    obstacle_type = random.choice(["tree", "rock"])
    cactus_width = TREE_WIDTH if obstacle_type == "tree" else ROCK_WIDTH
    cactus_height = TREE_HEIGHT if obstacle_type == "tree" else ROCK_HEIGHT
    display.fill(0)
    display.text("DINO RUN", 35, 10)
    display.text("Score " + str(WIN_SCORE) + " to win", 10, 30)
    display.text("Press to Start", 10, 48)
    display.show()
    play_startup_music()
    while button.value(): # Wait for press
        pass
    buzz(100, 440)

# --- Main Game Loop ---
reset_game()

while True:
    display.fill(0)
    
    # 1. Handle Input (Jump)
    if not button.value() and not is_jumping:
        velocity_y = jump_strength
        is_jumping = True
        buzz(30, 880) # Jump sound

    # 2. Physics / Gravity
    dino_y += velocity_y
    velocity_y += gravity
    
    # Ground collision
    if dino_y >= GROUND_Y - DINO_HEIGHT:
        dino_y = GROUND_Y - DINO_HEIGHT
        velocity_y = 0
        is_jumping = False

    # 3. Move Obstacle (Cactus)
    cactus_x -= game_speed
    if cactus_x < -cactus_width:
        cactus_x = SCREEN_WIDTH + random.randint(0, 40)
        obstacle_type = random.choice(["tree", "rock"])
        cactus_width = TREE_WIDTH if obstacle_type == "tree" else ROCK_WIDTH
        cactus_height = TREE_HEIGHT if obstacle_type == "tree" else ROCK_HEIGHT
        score += 1
        # Check for win!
        if score >= WIN_SCORE:
            if lives == 3:
                fireworks(display)
                play_victory_music()
            else:
                buzz(200, 880)
                buzz(200, 1047)
            display.fill(0)
            display.text("YOU WIN!", 32, 10)
            display.text("Score: " + str(score), 35, 25)
            display.text("Lives: " + str(lives) + "/3", 35, 40)
            if lives == 3:
                display.text("PERFECT!", 35, 52)
            display.show()
            time.sleep(3)
            reset_game()
            continue
        # Increase difficulty slightly
        if score % 5 == 0:
            game_speed += 1
            buzz(50, 1200) # Level up blip

    # 4. Collision Detection
    # Checking if Dino rectangle overlaps Cactus rectangle
    if (DINO_X < cactus_x + cactus_width and
        DINO_X + DINO_WIDTH > cactus_x and
        dino_y < GROUND_Y and
        dino_y + DINO_HEIGHT > GROUND_Y - cactus_height):
        
        # Collision Occurred!
        lives -= 1
        buzz(200, 150) # Crash sound
        if lives <= 0:
            buzz(400, 100)
            display.fill(0)
            display.text("GAME OVER", 30, 20)
            display.text("Score: " + str(score), 35, 35)
            display.show()
            time.sleep(2)
            reset_game()
        else:
            # Lost a life but keep going
            display.fill(0)
            display.text("OUCH!", 45, 20)
            display.text("Lives: " + str(lives), 40, 35)
            display.show()
            time.sleep(1)
            # Move obstacle away so dino doesn't re-collide
            cactus_x = SCREEN_WIDTH + random.randint(20, 60)

    # 5. Drawing
    # Draw Ground
    display.hline(0, GROUND_Y, 128, 1)
    
    # Draw Dino sprite
    display.blit(DINO_FB, DINO_X, int(dino_y))
    
    # Draw Obstacle sprite
    obs_y = GROUND_Y - cactus_height
    if obstacle_type == "tree":
        display.blit(TREE_FB, cactus_x, obs_y)
    else:
        display.blit(ROCK_FB, cactus_x, obs_y)
    
    # Draw Lives (hearts)
    for i in range(lives):
        display.text("*", i * 8, 5)
    
    # Draw Score
    display.text(str(score), 110, 5)
    
    display.show()
    time.sleep_ms(30)