import sys
from time import time
HEADER_SIZE = 12

class RtpPacket:
    # Define Constants for bit manipulation
    VERSION_SHIFT = 6
    PADDING_SHIFT = 5
    EXTENSION_SHIFT = 4
    CC_MASK = 0x0F
    MARKER_SHIFT = 7
    PT_MASK = 0x7F

    def __init__(self):
        self.header = bytearray(HEADER_SIZE)
        self.payload = b''
        
    def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload, timestamp):
        """Encode the RTP packet with header fields and payload."""
        self.header[0] = (version << self.VERSION_SHIFT) | (padding << self.PADDING_SHIFT) | (extension << self.EXTENSION_SHIFT) | (cc & self.CC_MASK)
        self.header[1] = (marker << self.MARKER_SHIFT) | (pt & self.PT_MASK)
        
        self.header[2] = (seqnum >> 8) & 0xFF
        self.header[3] = seqnum & 0xFF
        
        self.header[4] = (timestamp >> 24) & 0xFF
        self.header[5] = (timestamp >> 16) & 0xFF
        self.header[6] = (timestamp >> 8) & 0xFF
        self.header[7] = timestamp & 0xFF
        
        self.header[8] = (ssrc >> 24) & 0xFF
        self.header[9] = (ssrc >> 16) & 0xFF
        self.header[10] = (ssrc >> 8) & 0xFF
        self.header[11] = ssrc & 0xFF
        
        # Get the payload from the argument
        self.payload = payload
        
    def decode(self, byteStream):
        """Decode the RTP packet."""
        self.header = bytearray(byteStream[:HEADER_SIZE])
        self.payload = byteStream[HEADER_SIZE:]
    
    def version(self):
        """Return RTP version."""
        return int(self.header[0] >> self.VERSION_SHIFT)
    
    def seqNum(self):
        """Return sequence (frame) number."""
        seqNum = self.header[2] << 8 | self.header[3]
        return int(seqNum)
    
    def timestamp(self):
        """Return timestamp."""
        timestamp = self.header[4] << 24 | self.header[5] << 16 | self.header[6] << 8 | self.header[7]
        return int(timestamp)
    
    def payloadType(self):
        """Return payload type."""
        pt = self.header[1] & self.PT_MASK
        return int(pt)
    
    def getPayload(self):
        """Return payload."""
        return self.payload
        
    def getPacket(self):
        """Return RTP packet."""
        return self.header + self.payload

    def getMarker(self):
        """Return marker bit."""
        return (self.header[1] >> self.MARKER_SHIFT) & 1