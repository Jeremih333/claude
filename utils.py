import math, os, subprocess
def seconds_to_hhmmssms(t):
    h = int(t//3600); m=int((t%3600)//60); s=int(t%60); ms=int((t-int(t))*1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"