from random import randint
import sys, traceback, threading, socket
from VideoStream import VideoStream
from RtpPacket import RtpPacket
import time 

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2
    
    clientInfo = {}
    
    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        self.rtpSequenceNumber = 0
        self.currRtpTimestamp = 0
        
    def run(self):
        """Start the thread to receive RTSP requests."""
        threading.Thread(target=self.recvRtspRequest).start()
    
    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:            
            try:
                data = connSocket.recv(256)
                if data:
                    print("Data received:\n" + data.decode("utf-8"))
                    self.processRtspRequest(data.decode("utf-8"))
                else:
                    break
            except:
                break
        connSocket.close()
        
    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        # Get the request type
        request = data.split('\r\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        
        # Get the media file name
        filename = line1[1]
        
        # Get the RTSP sequence number 
        seq = request[1].split(' ')
        
        # Process SETUP request
        if requestType == self.SETUP:
            if self.state == self.INIT:
                # Update state
                print("processing SETUP\n")
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                    # Get total frames to send to Client for the time bar
                    total_frames = self.clientInfo['videoStream'].totalFrames
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                
                # Generate a randomized RTSP session ID
                self.clientInfo['session'] = randint(100000, 999999)
                
                # Send RTSP reply
                self.replyRtsp(self.OK_200, seq[1], total_frames)
                
                # Get the RTP/UDP port from the last line
                transport_line = ""
                for line in request:
                    if "client_port" in line:
                        transport_line = line
                        break
                if transport_line:
                    self.clientInfo['rtpPort'] = transport_line.split("client_port=")[1].split('-')[0]
                self.clientInfo['clientAddress'] = self.clientInfo['rtspSocket'][1][0]
        
        # Process PLAY request      
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                self.state = self.PLAYING
                
                # Create a new socket for RTP/UDP
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.replyRtsp(self.OK_200, seq[1])
                
                # Create a new thread and start sending RTP packets
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
                self.clientInfo['worker'].start()
        
        # Process PAUSE request
        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY
                self.clientInfo['event'].set()
                self.replyRtsp(self.OK_200, seq[1])
        
        # Process TEARDOWN request
        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")
            self.clientInfo['event'].set()
            self.replyRtsp(self.OK_200, seq[1])
            # [FIXED CRASH 10038] Do not close the socket here.
            # Let sendRtp function close it automatically when the thread ends.
            # if 'rtpSocket' in self.clientInfo:
            #     self.clientInfo['rtpSocket'].close()
            
    def sendRtp(self):
        """Send RTP packets - Optimized for HD Streaming & Smoothness."""
        MAX_RTP_PAYLOAD = 1400 
        VIDEO_CLOCK_RATE = 90000
        FPS = 20
        FRAME_PERIOD = 1.0 / FPS
        TIMESTAMP_INCREMENT = VIDEO_CLOCK_RATE / FPS
        
        # Stream start time
        next_frame_time = time.time()

        while True:
            # [NEW LOGIC] Calculate exact wait time
            # Helps video play smoothly and maximizes CPU rest (equivalent to wait ~0.05s)
            current_time = time.time()
            time_to_wait = next_frame_time - current_time

            if time_to_wait > 0:
                self.clientInfo['event'].wait(time_to_wait)
                # Check event again after waking up
                if self.clientInfo['event'].isSet(): 
                    break 
            else:
                # If late, check event quickly
                if self.clientInfo['event'].isSet(): 
                    break 

            # Time to send frame
            data = self.clientInfo['videoStream'].nextFrame()
            
            if data: 
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                try:
                    address = self.clientInfo['clientAddress']
                    port = int(self.clientInfo['rtpPort'])
                    
                    self.currRtpTimestamp += TIMESTAMP_INCREMENT
                    rtp_timestamp = int(self.currRtpTimestamp)

                    data_len = len(data)
                    curr_pos = 0
                    packets_sent_count = 0 
                    
                    while curr_pos < data_len:
                        chunk = data[curr_pos : curr_pos + MAX_RTP_PAYLOAD]
                        curr_pos += MAX_RTP_PAYLOAD
                        
                        marker = 1 if curr_pos >= data_len else 0
                        self.rtpSequenceNumber += 1

                        self.clientInfo['rtpSocket'].sendto(
                            self.makeRtp(chunk, frameNumber, marker, self.rtpSequenceNumber, rtp_timestamp),
                            (address, port)
                        )
                        packets_sent_count += 1
                        # Reduce network buffer load
                        if packets_sent_count % 100 == 0:
                            time.sleep(0.001)
                    
                    # Calculate time for the next frame
                    next_frame_time += FRAME_PERIOD
                    
                    # [LAG HANDLING] Reset clock if delayed too much (>0.5s)
                    if time.time() > next_frame_time + 0.5:
                        next_frame_time = time.time()

                except Exception as e:
                    print(f"Connection Error: {e}")
                    break
            else:
                print("End of Stream")
                self.state = self.READY
                break
        
        # Safely close socket here when thread ends
        if 'rtpSocket' in self.clientInfo:
            try:
                self.clientInfo['rtpSocket'].close()
                print("Server RTP Socket closed.")
            except:
                pass

    def makeRtp(self, payload, frameNbr, marker, seqNum, timestamp):
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26 # MJPEG type
        ssrc = 0 
        rtpPacket = RtpPacket()
        rtpPacket.encode(version, padding, extension, cc, seqNum, marker, pt, ssrc, payload, timestamp)
        return rtpPacket.getPacket()
        
    def replyRtsp(self, code, seq, extra_data=None):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session']) + '\n'
            if extra_data is not None:
                reply += 'x-Total-Frames: ' + str(extra_data) + '\n'
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
            
        # Error messages
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:  
            print("500 CONNECTION ERROR")