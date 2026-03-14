# Minimal TLS test - upload to device and run:  exec(open('tls_test.py').read())
# Tests if HTTPS is possible on this ESP32 at all

import gc, json, time, socket, network

gc.collect()
print('1. Python free:', gc.mem_free())

import esp32
heap = esp32.idf_heap_info(esp32.HEAP_DATA)
print('2. IDF heap BEFORE WiFi:', heap)
# Find largest contiguous block
max_contig = max(h[2] for h in heap)
print('   Largest contiguous:', max_contig)

# Connect WiFi
try:
    with open('wifi_config.json', 'r') as f:
        nets = json.load(f)
except:
    nets = []

if not nets:
    print('ERROR: No wifi_config.json found')
    raise SystemExit

sta = network.WLAN(network.STA_IF)
sta.active(True)
ssid = nets[0]['ssid']
sta.connect(ssid, nets[0].get('password', ''))
print('3. Connecting to', ssid, '...')
t = time.time()
while not sta.isconnected():
    if time.time() - t > 15:
        print('ERROR: WiFi timeout')
        raise SystemExit
    time.sleep(0.5)
print('   Connected! IP:', sta.ifconfig()[0])

gc.collect()
print('4. Python free after WiFi:', gc.mem_free())
heap = esp32.idf_heap_info(esp32.HEAP_DATA)
print('5. IDF heap AFTER WiFi:', heap)
max_contig = max(h[2] for h in heap)
print('   Largest contiguous:', max_contig)

# Try TLS
print('6. Attempting TLS handshake to www.oref.org.il...')
gc.collect()
import ssl
gc.collect()
print('   Python free before TLS:', gc.mem_free())
heap = esp32.idf_heap_info(esp32.HEAP_DATA)
print('   IDF heap before TLS:', heap)
max_contig = max(h[2] for h in heap)
print('   Largest contiguous:', max_contig)

try:
    addr = socket.getaddrinfo('www.oref.org.il', 443)[0][-1]
    s = socket.socket()
    s.settimeout(10)
    s.connect(addr)
    ss = ssl.wrap_socket(s, server_hostname='www.oref.org.il')
    print('7. TLS SUCCESS!')
    print('   Python free after TLS:', gc.mem_free())
    heap = esp32.idf_heap_info(esp32.HEAP_DATA)
    print('   IDF heap after TLS:', heap)
    
    # Try actual request
    ss.write(b'GET /WarningMessages/alert/alerts.json HTTP/1.1\r\nHost: www.oref.org.il\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n')
    resp = b''
    while True:
        chunk = ss.read(512)
        if not chunk:
            break
        resp += chunk
    ss.close()
    print('8. Response length:', len(resp))
    print('   Response preview:', resp[:200])
except Exception as e:
    print('7. TLS FAILED:', e)
    heap = esp32.idf_heap_info(esp32.HEAP_DATA)
    print('   IDF heap at failure:', heap)

print('--- TEST COMPLETE ---')
