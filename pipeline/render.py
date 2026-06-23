# Rendering helpers (if needed)
def concat_clips(clips, out_path):
    # clips: list of file paths
    import subprocess, tempfile, os
    tf = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.txt')
    for c in clips:
        tf.write(f"file '{os.path.abspath(c)}'\n")
    tf.flush(); tf.close()
    cmd = f"ffmpeg -y -f concat -safe 0 -i {tf.name} -c copy {out_path} -hide_banner -loglevel error"
    os.system(cmd)
    os.unlink(tf.name)
    return out_path