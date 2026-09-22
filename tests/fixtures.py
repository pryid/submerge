"""Synthetic subscription data; keys and endpoints are not deployment credentials."""

import base64
from html.parser import HTMLParser
from urllib.parse import urlencode

WG_LINK = (
    "wireguard://AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA%3D@node.example.com:51820?"
    + urlencode(
        {
            "publickey": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
            "address": "192.0.2.2/32",
            "dns": "192.0.2.53",
        }
    )
)

AWG_CONFIG = """[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 192.0.2.2/32
DNS = 192.0.2.53
MTU = 1420
Jc = 4
Jmin = 40
Jmax = 70
S1 = 30
S2 = 40
H1 = 1
H2 = 2
H3 = 3
H4 = 4
I1 = <b 0x1234><r 10>

# Demo AWG
[Peer]
PublicKey = AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = node.example.com:51820
PersistentKeepalive = 25"""


def vpn_link(config=AWG_CONFIG):
    return "vpn://" + base64.urlsafe_b64encode(config.encode()).decode().rstrip("=")


class SubscriptionHTML(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.rows = []
        self.downloads = []
        self.tags = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "button" and attrs.get("class") == "row":
            self.rows.append(attrs)
        if tag == "a" and "download" in attrs:
            self.downloads.append(attrs)
