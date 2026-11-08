import socket

message = (
    'M-SEARCH * HTTP/1.1\r\n'
    'HOST: 239.255.255.250:1900\r\n'
    'MAN: "ssdp:discover"\r\n'
    'MX: 2\r\n'
    'ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n'
).encode()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.settimeout(3)
sock.sendto(message, ('239.255.255.250', 1900))
seen = set()
try:
    while True:
        data, address = sock.recvfrom(65535)
        text = data.decode(errors='replace')
        if text not in seen:
            print(address)
            print(text)
            seen.add(text)
except socket.timeout:
    pass
