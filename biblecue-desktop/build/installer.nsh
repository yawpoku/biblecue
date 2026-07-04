; Override LZMA solid compression — avoids NSIS 3.0.4.1 mmap bug on large apps
SetCompressor /FINAL zlib
