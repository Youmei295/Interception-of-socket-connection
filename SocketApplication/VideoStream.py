import os

class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            self.file = open(filename, 'rb')
            self.frameNum = 0
            self.buffer = bytearray() # Use bytearray instead of bytes for memory optimization
            self.eof = False
            
            # Automatically detect file type
            self.fileType = self.detectFileType()
            print(f"File: {filename} | Detected Type: {self.fileType}")

            # Count total frames
            self.totalFrames = self.countFrames()
            
        except:
            raise IOError
    
    def detectFileType(self):
        """Check if the file is the proprietary Lab format or standard MJPEG."""
        pos = self.file.tell()
        self.file.seek(0)
        header = self.file.read(5)
        self.file.seek(pos)
        
        if len(header) == 5 and header.isdigit():
            return "LAB_PROPRIETARY"
        else:
            return "STANDARD_MJPEG"

    def nextFrame(self):
        """Router: Select the optimal processing function for each file type."""
        if self.fileType == "LAB_PROPRIETARY":
            return self.nextFrameLab()
        else:
            return self.nextFrameStandard()

    def nextFrameLab(self):
        """
        [FOR EXPERIMENTAL FILE - movie.Mjpeg]
        Logic: Read exactly 5 header bytes -> get length -> read exact length bytes.
        Advantage: Absolute accuracy for the assignment's custom file.
        """
        try:
            length_str = self.file.read(5)
            if not length_str:
                return None 
            
            length = int(length_str)
            data = self.file.read(length)
            if len(data) != length:
                return None 
                
            self.frameNum += 1
            return data
        except:
            return None

    def nextFrameStandard(self):
        # Increase chunk size to 5MB to read multiple HD frames at once.
        # Helps find() work faster by reducing string concatenation/disk I/O.
        READ_CHUNK_SIZE = 5 * 1024 * 1024 
        
        try:
            while True:
                # 1. Find JPEG Start of Image (0xFF 0xD8)
                start_pos = self.buffer.find(b'\xff\xd8')
                
                if start_pos != -1:
                    # 2. Find JPEG End of Image (0xFF 0xD9) AFTER the start position
                    end_pos = self.buffer.find(b'\xff\xd9', start_pos)
                    
                    if end_pos != -1:
                        frame_data = self.buffer[start_pos : end_pos + 2]
                        self.buffer = self.buffer[end_pos + 2:]
                        self.frameNum += 1
                        return frame_data
                
                # If buffer is too large and no frame is found (garbage), clean up.
                if len(self.buffer) > READ_CHUNK_SIZE * 2:
                     # Keep the last part to avoid cutting the header
                     self.buffer = self.buffer[-1024:]

                if self.eof:
                    return None
                    
                new_data = self.file.read(READ_CHUNK_SIZE)
                if not new_data:
                    self.eof = True
                    if not self.buffer:
                        return None
                
                self.buffer += new_data
                
        except Exception as e:
            print(f"Error reading frame: {e}")
            return None
        
    def frameNbr(self):
        """Get the current frame number."""
        return self.frameNum
        
    def countFrames(self):
        """Fast frame counting (Header scan)."""
        current_pos = self.file.tell() 
        self.file.seek(0)
        count = 0
        
        if self.fileType == "LAB_PROPRIETARY":
            while True:
                l_str = self.file.read(5)
                if not l_str: break
                try:
                    l = int(l_str)
                    self.file.seek(l, 1)
                    count += 1
                except: break
        else:
            # Fast scan for real-world files
            SCAN_CHUNK = 1024 * 1024 * 2 # 2MB
            temp_buff = b''
            while True:
                data = self.file.read(SCAN_CHUNK)
                if not data: break
                temp_buff += data
                # Simplified scan logic for fast counting (only count FFD8)
                count += temp_buff.count(b'\xff\xd8')
                # Keep the last part in case of split markers
                temp_buff = temp_buff[-2:]
        
        self.file.seek(current_pos)
        self.buffer = bytearray()
        self.eof = False
        print(f"Total Frames counted: {count}")
        return count