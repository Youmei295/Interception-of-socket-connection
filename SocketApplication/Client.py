from tkinter import *
import tkinter.messagebox as mb
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import queue
import time
import io
from RtpPacket import RtpPacket

# [NOTE] Removed CACHE_FILE_NAME and CACHE_FILE_EXT
# Reason: Using io.BytesIO (RAM) is significantly faster than writing to disk for every frame.

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT
    
    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3
    
    BUFFER_SIZE = 20
    FPS = 20              
    
    TOTAL_FRAME_ESTIMATE = 0 

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        """
        Initialize the Client application.
        :param master: The Tkinter root object.
        :param serveraddr: IP address of the server.
        :param serverport: RTSP port of the server.
        :param rtpport: RTP port to receive video data.
        :param filename: Name of the video file to request.
        """
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        
        self.totalPackets = 0
        self.lostPackets = 0
        self.totalBytes = 0
        self.startTime = 0
        self.lastSeqNum = -1
        self.lostFrames = 0 
        
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0
        
        self.lastGoodFrame = None 
        self.currentRtpTimestamp = -1
        
        self.frameBuffer = queue.Queue()
        self.isBuffering = True 
        self.threadsCreated = False 
        self.killThreads = False
        self.nextFrameTime = 0 

    def createWidgets(self):
        """
        Build the GUI components (Video frame, Buttons, Progress bar, Labels).
        """
        self.master.configure(bg="#2c3e50") 
        
        # Video Frame: configured to auto-resize based on image content
        self.videoFrame = Frame(self.master, bg="black", bd=2, relief="sunken")
        self.videoFrame.grid(row=0, column=0, columnspan=5, padx=10, pady=10, sticky="nsew")
        
        self.label = Label(self.videoFrame, bg="black")
        self.label.pack(fill="both", expand=True)
        
        # Added 'width' and 'anchor' to prevent UI jittering/resizing when text changes
        # width=22: Reserves space for "00:00:00 / 00:00:00"
        self.timeLabel = Label(self.master, text="00:00:00 / --:--:--", font=("Consolas", 10, "bold"), 
                               bg="#2c3e50", fg="white", width=22, anchor='w')
        self.timeLabel.grid(row=1, column=0, columnspan=1, sticky="w", padx=10)
        
        # Added width=12 for Buffer label
        self.bufferLabel = Label(self.master, text="Buffer: 0", font=("Consolas", 10), 
                                 bg="#2c3e50", fg="#bdc3c7", width=12, anchor='w')
        self.bufferLabel.grid(row=1, column=1, sticky="w", padx=5)
        
        # Added width=85 for Stats label to accommodate long strings without resizing window
        self.statsLabel = Label(self.master, text="Init...", font=("Consolas", 9), 
                                bg="#2c3e50", fg="#f1c40f", width=85, anchor='e')
        self.statsLabel.grid(row=1, column=2, columnspan=3, sticky="e", padx=10)
        
        self.progressCanvas = Canvas(self.master, height=12, bg="#34495e", highlightthickness=0)
        self.progressCanvas.grid(row=2, column=0, columnspan=5, sticky="we", padx=10, pady=(0, 5))
        
        self.bufferBar = self.progressCanvas.create_rectangle(0, 0, 0, 12, fill="#95a5a6", width=0)
        self.playBar = self.progressCanvas.create_rectangle(0, 0, 0, 12, fill="#e74c3c", width=0)
        
        self.buttonFrame = Frame(self.master, bg="#2c3e50")
        self.buttonFrame.grid(row=3, column=0, columnspan=5, pady=10)
        
        btn_config = {'width': 12, 'padx': 5, 'pady': 5, 'font': ("Segoe UI", 10, "bold"), 'bg': '#ecf0f1'}
        
        self.setup = Button(self.buttonFrame, text="SETUP", command=self.setupMovie, **btn_config)
        self.setup.pack(side="left", padx=5)
        
        self.start = Button(self.buttonFrame, text="PLAY", command=self.playMovie, **btn_config)
        self.start.pack(side="left", padx=5); self.start["state"] = "disabled"
        
        self.pause = Button(self.buttonFrame, text="PAUSE", command=self.pauseMovie, **btn_config)
        self.pause.pack(side="left", padx=5); self.pause["state"] = "disabled"
        
        self.teardown = Button(self.buttonFrame, text="TEARDOWN", command=self.exitClient, **btn_config) 
        self.teardown.pack(side="left", padx=5)

    def setupMovie(self):
        """Handler for Setup button."""
        if self.state == self.INIT: self.sendRtspRequest(self.SETUP)
    
    def exitClient(self):
        """
        Teardown connection and close application gracefully.
        Handles thread termination and socket cleanup.
        """
        # [STEP 1] Send TEARDOWN request first
        # Purpose: Notify the server to stop the video stream thread and release server-side resources.
        try:
            if self.state != self.INIT:
                self.sendRtspRequest(self.TEARDOWN)
        except:
            pass

        # [STEP 2] Set flags to signal threads to stop
        self.killThreads = True
        self.teardownAcked = 1  # Simulate ACK receipt to force loops to exit immediately
        
        # [STEP 3] Close Sockets
        
        # 3a. Close RTP Socket (UDP)
        # CRITICAL: This action forces the blocking recv() call in the listenRtp thread to raise an exception.
        # This breaks the loop and allows the thread to terminate.
        try:
            self.rtpSocket.close()
        except:
            pass

        # 3b. Close RTSP Socket (TCP)
        try:
            # shutdown() ensures the connection is closed cleanly before destroying the socket object
            self.rtspSocket.shutdown(socket.SHUT_RDWR)
            self.rtspSocket.close()
        except:
            pass

        # [STEP 4] Destroy the GUI
        try:
            self.master.destroy()
        except:
            pass

    def pauseMovie(self):
        """Handler for Pause button."""
        if self.state == self.PLAYING: self.sendRtspRequest(self.PAUSE)
    
    def playMovie(self):
        """Handler for Play button."""
        if self.state == self.READY:
            if self.frameBuffer.qsize() == 0:
                self.isBuffering = True
                self.frameBuffer.queue.clear()
            else:
                self.isBuffering = False 
            
            self.nextFrameTime = time.time()
            if self.startTime == 0: self.startTime = time.time()
            
            # If threads are not created yet, start them
            if not self.threadsCreated:
                threading.Thread(target=self.listenRtp).start() 
                threading.Thread(target=self.consumeBuffer).start()
                self.threadsCreated = True
            self.sendRtspRequest(self.PLAY)
    
    def updateProgressBar(self):
        """
        Update the GUI progress bar, time label, and statistics label.
        Calculates buffering ratio, play ratio, and packet loss statistics.
        """
        if self.killThreads: return
        try:
            # Check if widget still exists before updating
            if not self.progressCanvas.winfo_exists(): return
            
            w = self.progressCanvas.winfo_width()
            if w <= 1: return 
            
            total = self.TOTAL_FRAME_ESTIMATE if self.TOTAL_FRAME_ESTIMATE > 0 else 1
            total_seconds = total / self.FPS
            
            current_ts = self.currentRtpTimestamp if self.currentRtpTimestamp > 0 else 0
            curr_seconds = current_ts / 90000.0 
            
            if total_seconds > 0:
                play_ratio = curr_seconds / total_seconds
            else:
                play_ratio = 0
            
            buffer_duration_in_seconds = self.frameBuffer.qsize() / self.FPS
            
            if total_seconds > 0:
                buf_ratio = (curr_seconds + buffer_duration_in_seconds) / total_seconds
            else:
                buf_ratio = 0
            
            if play_ratio > 1: play_ratio = 1
            if buf_ratio > 1: buf_ratio = 1
            
            self.progressCanvas.coords(self.bufferBar, 0, 0, w * buf_ratio, 12)
            self.progressCanvas.coords(self.playBar, 0, 0, w * play_ratio, 12)
            
            self.timeLabel.configure(text=f"{time.strftime('%H:%M:%S', time.gmtime(int(curr_seconds)))} / {time.strftime('%H:%M:%S', time.gmtime(int(total_seconds)))}")
            
            duration = time.time() - self.startTime
            if duration > 0:
                kbps = (self.totalBytes / 1024) / duration
                pkt_loss_rate = 0
                if (self.totalPackets + self.lostPackets) > 0:
                    pkt_loss_rate = (self.lostPackets / (self.totalPackets + self.lostPackets)) * 100
                usage_mb = self.totalBytes / (1024 * 1024)
                
                frame_loss_rate = 0
                total_frames_processed = self.frameNbr + self.lostFrames
                if total_frames_processed > 0:
                    frame_loss_rate = (self.lostFrames / total_frames_processed) * 100
                
                stats_text = (f"Pkt Loss: {pkt_loss_rate:.2f}% | Frame Loss: {frame_loss_rate:.2f}% ({self.lostFrames}) | Rate: {kbps:.2f} KB/s | Size: {usage_mb:.2f} MB")
                self.statsLabel.configure(text=stats_text)
            self.bufferLabel.configure(text=f"Buffer: {self.frameBuffer.qsize()}")
        except Exception:
            pass

    def safeUpdateImage(self, pil_image):
        """
        Update the image label in a thread-safe manner.
        :param pil_image: PIL Image object to display.
        """
        if self.killThreads: return
        try:
            photo = ImageTk.PhotoImage(pil_image)
            self.label.configure(image=photo) 
            self.label.image = photo
        except: 
            pass

    def consumeBuffer(self):
        """
        Thread function to consume frames from the buffer and display them.
        Handles buffering logic and frame timing (FPS control).
        """
        while True:
            if self.killThreads or self.teardownAcked == 1: break
            
            if self.state != self.PLAYING:
                self.nextFrameTime = time.time()
                time.sleep(0.1)
                continue
            
            # Buffering Logic
            if self.isBuffering:
                if self.frameBuffer.qsize() >= self.BUFFER_SIZE:
                    self.isBuffering = False 
                    self.nextFrameTime = time.time()
                else:
                    if self.frameBuffer.qsize() > 0: 
                        try: self.master.after_idle(self.updateProgressBar)
                        except: break
                    time.sleep(0.05)
                    continue
            
            if not self.frameBuffer.empty():
                frame_data = self.frameBuffer.get()
                self.frameNbr += 1
                
                try:
                    # Use io.BytesIO for in-memory image processing (No cache file needed)
                    image = Image.open(io.BytesIO(frame_data))
                    
                    # "Max HD" Logic
                    # Check original image dimensions
                    orig_w, orig_h = image.size
                    
                    # Resize only if image is larger than HD (1280x720)
                    # If image is small (e.g. 320x240), keep original size for clarity
                    if orig_w > 1280 or orig_h > 720:
                        image.thumbnail((1280, 720), Image.NEAREST)
                    
                    try:
                        # Schedule UI updates in the main thread
                        self.master.after_idle(lambda img=image: self.safeUpdateImage(img))
                        self.master.after_idle(self.updateProgressBar) 
                    except:
                        break
                except:
                    pass

                # Calculate Sleep Time to maintain FPS
                base_interval = 1.0 / self.FPS
                q_size = self.frameBuffer.qsize()
                
                # Dynamic speed adjustment based on buffer health
                if q_size < 10: actual_interval = base_interval * 1.05 # Slow down
                elif q_size > 50: actual_interval = base_interval * 0.90 # Speed up
                else: actual_interval = base_interval

                self.nextFrameTime += actual_interval
                sleep_time = self.nextFrameTime - time.time()
                if sleep_time > 0: time.sleep(sleep_time)
                else: 
                    # If lagging behind more than 0.5s, reset timer
                    if sleep_time < -0.5: self.nextFrameTime = time.time()
            else:
                self.isBuffering = True 

    def listenRtp(self):        
        """
        Thread function to listen for incoming RTP packets.
        Handles packet reassembly and packet loss detection.
        """
        currentFrameBuffer = b'' 
        self.currFrameMissing = False     
        self.currentRtpTimestamp = -1 
        
        while True:
            if self.killThreads or self.teardownAcked == 1: break
            try:
                data = self.rtpSocket.recv(40960) 
                if data:
                    self.totalBytes += len(data)
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    self.totalPackets += 1
                    
                    # Sequence Number checking for packet loss
                    currSeq = rtpPacket.seqNum()
                    if self.lastSeqNum != -1:
                        diff = currSeq - self.lastSeqNum
                        if diff < -50000: diff += 65536 # Handle seq wrap-around
                        if diff > 1:
                            self.lostPackets += (diff - 1)
                            self.currFrameMissing = True
                    self.lastSeqNum = currSeq
                    
                    # Timestamp checking for new frame
                    newTimestamp = rtpPacket.timestamp()
                    if self.currentRtpTimestamp != -1 and newTimestamp != self.currentRtpTimestamp:
                        # New frame started, push the previous one if valid
                        if len(currentFrameBuffer) > 0:
                            if self.currFrameMissing: self.lostFrames += 1
                            if self.lastGoodFrame is not None and self.frameBuffer.qsize() < 200:
                                self.frameBuffer.put(self.lastGoodFrame) # Concealment
                            currentFrameBuffer = b''
                            self.currFrameMissing = False 
                    
                    self.currentRtpTimestamp = newTimestamp
                    currentFrameBuffer += rtpPacket.getPayload()
                    
                    # Marker bit indicates end of frame
                    if rtpPacket.getMarker() == 1:
                        if not self.currFrameMissing:
                            if self.frameBuffer.qsize() < 200: 
                                self.frameBuffer.put(currentFrameBuffer)
                            self.lastGoodFrame = currentFrameBuffer
                        else:
                            self.lostFrames += 1
                            if self.lastGoodFrame is not None and self.frameBuffer.qsize() < 200:
                                self.frameBuffer.put(self.lastGoodFrame)
                        currentFrameBuffer = b''
                        self.currFrameMissing = False
            except: continue

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            mb.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
    
    def sendRtspRequest(self, requestCode):
        """
        Send RTSP request to the server.
        :param requestCode: constant (SETUP, PLAY, PAUSE, TEARDOWN)
        """
        
        # Setup request
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq+=1
            request = f"SETUP {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nTransport: RTP/AVP;client_port={self.rtpPort}\r\n"
            self.requestSent = self.SETUP
        
        # Play request
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq+=1
            request = f"PLAY {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nSession: {self.sessionId}\r\n"
            self.requestSent = self.PLAY
            
        # Pause request
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq+=1
            request = f"PAUSE {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nSession: {self.sessionId}\r\n"
            self.requestSent = self.PAUSE
            
        # Teardown request
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq+=1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nSession: {self.sessionId}\r\n" 
            self.requestSent = self.TEARDOWN
        else: return
        
        # Send the RTSP request using rtspSocket.
        self.rtspSocket.sendall(request.encode('utf-8'))
        print('\nData sent:\n' + request)
    
    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply: 
                    self.parseRtspReply(reply.decode("utf-8"))
                    
                # Close the RTSP socket upon requesting Teardown
                if self.requestSent == self.TEARDOWN:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    self.rtspSocket.close()
                    break
            except: break
    
    def parseRtspReply(self, data):
        """
        Parse the RTSP reply from the server.
        Extracts Sequence Number, Session ID, and Total Frames.
        """
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])
        for line in lines:
            if "x-Total-Frames" in line:
                try: self.TOTAL_FRAME_ESTIMATE = int(line.split(':')[1].strip())
                except: pass
                
        # Process only if the server reply's sequence number is the same as the request's
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            # New RTSP session ID
            if self.sessionId == 0: 
                self.sessionId = session
                
            # Process only if the session ID is the same
            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200: 
                    if self.requestSent == self.SETUP:
                        # Update RTSP state.
                        self.state = self.READY
                        
                        # Open RTP port.
                        self.openRtpPort()
                        self.setup["state"] = "disabled"
                        self.start["state"] = "normal"
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                        self.start["state"] = "disabled"
                        self.pause["state"] = "normal"
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        self.start["state"] = "normal"
                        self.pause["state"] = "disabled"
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        
                        # Flag the teardownAcked to close the socket.
                        self.teardownAcked = 1 
                        self.killThreads = True
    
    def openRtpPort(self):
        """Open RTP socket binded to a specified port."""
        # Create a new datagram socket to receive RTP packets from the server
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 10 * 1024 * 1024)
        # Set the timeout value of the socket to 0.5sec
        self.rtpSocket.settimeout(0.5)
        try: 
            # Bind the socket to the address using the RTP port given by the client user
            self.rtpSocket.bind(('', self.rtpPort))
        except: 
            mb.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if mb.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else: # When the user presses cancel, resume playing.
            self.playMovie()