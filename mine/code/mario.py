from machine import Pin, I2C, PWM
import ssd1306
import time
import random
import framebuf
import math

# --- Hardware Setup ---
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=400000)
display = ssd1306.SSD1306_I2C(128, 64, i2c)
button = Pin(4, Pin.IN, Pin.PULL_UP)
buzzer = PWM(Pin(23))

time.sleep(2)  # Ctrl+C window

def buzz(ms, freq):
    if freq > 0:
        buzzer.freq(freq)
        buzzer.duty(512)
    time.sleep_ms(ms)
    buzzer.duty(0)

def coin_sound():
    buzz(60, 988)
    buzz(100, 1319)

def jump_sound():
    for f in range(400, 700, 60):
        buzz(6, f)

def stomp_sound():
    buzz(50, 400)
    buzz(60, 200)

def death_sound():
    for f in range(500, 150, -40):
        buzz(35, f)
    buzz(250, 100)

def mario_theme():
    notes = [
        (660, 100), (660, 100), (0, 80), (660, 100), (0, 80),
        (524, 100), (660, 100), (0, 80), (784, 150), (0, 120),
        (392, 150),
    ]
    for freq, ms in notes:
        buzz(ms, freq)
        time.sleep_ms(25)

def win_tune():
    for freq, ms in [(524,80),(660,80),(784,80),(1048,120),(0,50),(784,80),(1048,200)]:
        buzz(ms, freq)
        time.sleep_ms(15)

# --- Sprites ---
# Mario 12x16
MARIO_BMP = bytearray([
    0b00000111, 0b11000000,
    0b00011111, 0b11110000,
    0b00011010, 0b10110000,
    0b00111010, 0b10100000,
    0b00111101, 0b01000000,
    0b00011111, 0b10000000,
    0b00000111, 0b11000000,
    0b00001111, 0b11100000,
    0b00111111, 0b11111000,
    0b01011111, 0b11110100,
    0b01111111, 0b11110100,
    0b01111111, 0b11111100,
    0b00011111, 0b11100000,
    0b00001101, 0b10000000,
    0b00011100, 0b11100000,
    0b00111100, 0b11110000,
])
MARIO_FB = framebuf.FrameBuffer(MARIO_BMP, 16, 16, framebuf.MONO_HLSB)

# Goomba 12x12 in 16x16
GOOMBA_BMP = bytearray([
    0b00000111, 0b11000000,
    0b00011111, 0b11110000,
    0b00111111, 0b11111000,
    0b01101111, 0b10110100,
    0b01100111, 0b00110100,
    0b01111111, 0b11111100,
    0b00111111, 0b11111000,
    0b00011111, 0b11110000,
    0b00001111, 0b11100000,
    0b00000111, 0b11000000,
    0b00001111, 0b11100000,
    0b00011111, 0b11110000,
    0b00111000, 0b01110000,
    0b01110000, 0b00111000,
    0b01110000, 0b00111000,
    0b00000000, 0b00000000,
])
GOOMBA_FB = framebuf.FrameBuffer(GOOMBA_BMP, 16, 16, framebuf.MONO_HLSB)

# Pipe 16x20 (draw as two rects instead for simplicity)
# Coin 8x8
COIN_BMP = bytearray([
    0b00111100,
    0b01111110,
    0b01100110,
    0b01100110,
    0b01100110,
    0b01100110,
    0b01111110,
    0b00111100,
])
COIN_FB = framebuf.FrameBuffer(COIN_BMP, 8, 8, framebuf.MONO_HLSB)

# Cloud 16x8
CLOUD_BMP = bytearray([
    0b00000110, 0b00000000,
    0b00001111, 0b01100000,
    0b00011111, 0b11110000,
    0b01111111, 0b11111100,
    0b11111111, 0b11111110,
    0b11111111, 0b11111110,
    0b01111111, 0b11111100,
    0b00011111, 0b11110000,
])
CLOUD_FB = framebuf.FrameBuffer(CLOUD_BMP, 16, 8, framebuf.MONO_HLSB)

# --- Constants ---
W = 128
H = 64
GROUND = 52
MX = 20  # Mario fixed screen X
MW = 12
MH = 16
GRAV = 2
JUMPV = -10
LEVEL_SEGS = 50

def draw_pipe(x, h):
    """Draw a pipe at screen x with height h from ground."""
    top = GROUND - h
    # Cap
    display.fill_rect(x - 2, top, 18, 4, 1)
    # Body
    display.fill_rect(x, top + 4, 14, h - 4, 1)
    # Hollow inside
    display.fill_rect(x + 3, top + 4, 8, h - 5, 0)

def draw_bricks(scroll_x):
    """Draw ground bricks, skipping gaps."""
    for bx in range(0, W, 8):
        wx = bx + scroll_x
        gap = False
        for t, s in level:
            if t == 'gap' and wx + 8 > s * 16 and wx < s * 16 + 24:
                gap = True
                break
        if not gap:
            display.rect(bx, GROUND, 8, 6, 1)
            if (bx // 8) % 2 == 0:
                display.vline(bx + 4, GROUND, 6, 1)

# --- Level & State ---
level = []
clouds_pos = []
collected = set()
mario_y = 0
vel_y = 0
jumping = False
scroll = 0
score = 0
coins = 0
lives = 3
spd = 3

def gen_level():
    global level, clouds_pos
    level = []
    clouds_pos = []
    i = 4
    while i < LEVEL_SEGS:
        r = random.randint(0, 10)
        if r <= 3:
            level.append(('pipe', i))
            i += 3
        elif r <= 6:
            level.append(('goomba', i))
            i += 3
        elif r <= 8:
            level.append(('coin', i))
            i += 2
        elif r == 9:
            level.append(('gap', i))
            i += 3
        else:
            i += 2
    for _ in range(5):
        clouds_pos.append((random.randint(0, LEVEL_SEGS * 16), random.randint(2, 15)))

def reset_round():
    global mario_y, vel_y, jumping, scroll, score, coins, spd, collected
    mario_y = GROUND - MH
    vel_y = 0
    jumping = False
    scroll = 0
    score = 0
    coins = 0
    spd = 3
    collected = set()
    gen_level()

def start_screen():
    display.fill(0)
    display.text("SUPER MARIO", 18, 4)
    display.text("ESP32 Edition", 12, 16)
    display.blit(MARIO_FB, 56, 28)
    display.text("Press button!", 14, 52)
    display.show()
    mario_theme()
    while button.value():
        pass
    time.sleep_ms(200)

def lose_life():
    global lives
    lives -= 1
    if lives <= 0:
        death_sound()
        display.fill(0)
        display.text("GAME OVER", 28, 15)
        display.text("Score:" + str(score), 30, 32)
        display.show()
        time.sleep(3)
        return True
    else:
        buzz(100, 250)
        display.fill(0)
        display.blit(MARIO_FB, 56, 20)
        display.text("x " + str(lives), 56, 40)
        display.show()
        time.sleep(1)
        reset_round()
        # Keep lives/score
        return False

def win():
    display.fill(0)
    display.text("LEVEL CLEAR!", 16, 4)
    display.text("Score:" + str(score), 30, 18)
    display.text("Coins:" + str(coins), 30, 28)
    if lives == 3:
        display.text("PERFECT RUN!", 16, 42)
    display.show()
    win_tune()
    if lives == 3:
        for _ in range(6):
            cx = random.randint(10, 118)
            cy = random.randint(5, 30)
            for r in range(3, 14, 2):
                for a in range(8):
                    ang = a * 0.785
                    px = int(cx + r * math.cos(ang))
                    py = int(cy + r * math.sin(ang))
                    if 0 <= px < 128 and 0 <= py < 64:
                        display.pixel(px, py, 1)
            display.show()
            buzz(12, random.randint(1000, 2000))
            time.sleep_ms(70)
    time.sleep(3)

# --- MAIN ---
lives = 3
reset_round()
start_screen()

while True:
    display.fill(0)
    
    # Jump
    if not button.value() and not jumping:
        vel_y = JUMPV
        jumping = True
        jump_sound()
    
    # Gravity
    vel_y += GRAV
    mario_y += vel_y
    
    # Check gap
    wx = scroll + MX
    in_gap = False
    for t, s in level:
        if t == 'gap' and wx + MW > s * 16 and wx < s * 16 + 24:
            in_gap = True
            break
    
    if in_gap:
        if mario_y > H + 10:
            if lose_life():
                lives = 3
                reset_round()
                start_screen()
            continue
    else:
        if mario_y >= GROUND - MH:
            mario_y = GROUND - MH
            vel_y = 0
            jumping = False
    
    # Scroll
    scroll += spd
    
    # Win check
    if scroll > LEVEL_SEGS * 16:
        win()
        lives = 3
        reset_round()
        start_screen()
        continue
    
    # Object interactions
    restart = False
    for idx, (t, s) in enumerate(level):
        ox = s * 16 - scroll
        if ox < -20 or ox > W + 10:
            continue
        
        if t == 'pipe':
            ph = 20
            ptop = GROUND - ph
            if (MX + MW > ox and MX < ox + 14 and
                mario_y + MH > ptop and mario_y < GROUND):
                if vel_y >= 0 and mario_y + MH - vel_y <= ptop + 4:
                    mario_y = ptop - MH
                    vel_y = 0
                    jumping = False
                else:
                    scroll -= spd
        
        elif t == 'goomba' and idx not in collected:
            gy = GROUND - 14
            if (MX + MW > ox + 2 and MX < ox + 12 and
                mario_y + MH > gy and mario_y < gy + 14):
                if vel_y > 0 and mario_y + MH < gy + 8:
                    collected.add(idx)
                    score += 100
                    vel_y = -6
                    stomp_sound()
                else:
                    if lose_life():
                        lives = 3
                        reset_round()
                        start_screen()
                    restart = True
                    break
        
        elif t == 'coin' and idx not in collected:
            cy = GROUND - 26
            if (MX + MW > ox + 2 and MX < ox + 10 and
                mario_y < cy + 8 and mario_y + MH > cy):
                collected.add(idx)
                coins += 1
                score += 50
                coin_sound()
    
    if restart:
        continue
    
    # Speed up
    if scroll > 0 and scroll % 250 < spd and spd < 5:
        spd += 1
    
    # --- Draw ---
    # Clouds
    for cx, cy in clouds_pos:
        sx = (cx - scroll // 2) % (LEVEL_SEGS * 16 + 128) - 16
        if -16 < sx < W:
            display.blit(CLOUD_FB, int(sx), cy)
    
    # Bricks
    draw_bricks(scroll)
    
    # Objects
    for idx, (t, s) in enumerate(level):
        ox = s * 16 - scroll
        if ox < -20 or ox > W + 10:
            continue
        if t == 'pipe':
            draw_pipe(int(ox), 20)
        elif t == 'goomba' and idx not in collected:
            display.blit(GOOMBA_FB, int(ox), GROUND - 14)
        elif t == 'coin' and idx not in collected:
            display.blit(COIN_FB, int(ox) + 4, GROUND - 26)
    
    # Mario
    display.blit(MARIO_FB, MX, int(mario_y))
    
    # HUD
    display.blit(MARIO_FB, 0, -2)  # tiny mario icon... too big, use text
    display.fill_rect(0, 0, 40, 9, 0)
    display.text("x" + str(lives), 1, 1)
    display.blit(COIN_FB, 48, 0)
    display.text(str(coins), 58, 1)
    display.text(str(score), W - len(str(score)) * 8, 1)
    
    display.show()
    time.sleep_ms(30)
