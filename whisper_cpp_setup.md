# whisper.cpp setup on Windows (recommended for fastest CPU transcription)

This guide helps you build and use whisper.cpp (ggml quantized models) on Windows. It gives best CPU performance.

## 1. Install dependencies
- Install Git, Python 3.10+, CMake, a C/C++ compiler (Visual Studio Build Tools).
- Install ffmpeg and add to PATH.

## 2. Clone repository
```bash
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
```
## 3. Build (using CMake)
Open 'x64 Native Tools Command Prompt for VS 2022' or similar:
```bash
mkdir build
cd build
cmake .. -G \"Visual Studio 17 2022\" -A x64
cmake --build . --config Release -j
```
This will generate the whisper.cpp.exe (or main.exe) in build folder.

## 4. Download quantized models
Use the ggml quantized models (q4_0, q5_0) from the repo's releases or from community mirrors.
Place the `.bin` model file in a models/ folder.

## 5. Run transcription
```bash
.\main.exe -m models/ggml-small.bin -f ../audio.wav
```
Refer to the whisper.cpp README for more options (language selection, timestamps, etc.)

## Notes
- whisper.cpp is fastest on CPU among open-source options due to ggml quantization.
- If you prefer Python-only route, `faster-whisper` may be easier to install via pip but can be slower.