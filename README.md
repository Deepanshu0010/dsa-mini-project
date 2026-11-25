# DSA Mini Project: Lossless Huffman File Compressor

A small Python command-line project that compresses a `.txt` file into a
binary `.huf` archive and restores it exactly. It works on the file's bytes,
so UTF-8 text, punctuation, whitespace, line endings, and other byte values
survive a round trip without text decoding or newline conversion.

## Requirements

- Python 3.10 or newer
- No third-party packages

## Use it

Open a terminal in this folder.

Compress a text file. By default, `notes.txt` becomes `notes.txt.huf`:

```powershell
python huffman_compressor.py compress notes.txt
```

Choose an archive name:

```powershell
python huffman_compressor.py compress notes.txt --output notes.huf
```

Decompress. By default, `notes.txt.huf` becomes `notes.txt`:

```powershell
python huffman_compressor.py decompress notes.txt.huf
```

Choose a restored file name:

```powershell
python huffman_compressor.py decompress notes.huf --output restored_notes.txt
```

## Use the webpage

Start the local web server from this folder:

```powershell
py web_app.py
```

Open `http://127.0.0.1:8000` in your browser. Choose **Compress file** or
**Decompress archive**, select or drop your file, then click the operation
button. The browser downloads the result. Keep the PowerShell window open while
using the page; press **Ctrl+C** there to stop the server. The webpage is served
from your computer, and the file operation runs in the local Python app.

The program reports the input and output sizes, compression mode, and bytes
saved (or the amount added for a file that does not compress well).

## How the algorithm works

1. Count how often each of the 256 possible byte values occurs.
2. Put the byte counts into a min-heap. Repeatedly remove the two least
   frequent nodes and join them until one Huffman tree remains.
3. Label each left edge `0` and each right edge `1`. Frequent bytes get shorter
   codes; less frequent bytes get longer codes.
4. Write the tree and the packed code bits to the archive. A one-symbol file
   is handled with a one-bit code, and an empty file needs no tree.
5. During decompression, follow each stored bit through the tree and write the
   recovered bytes. The header's original size and bit count are used to check
   that the archive is complete.

If the Huffman tree and encoded data would take more space than the original
payload, the program stores the bytes in raw mode inside the same archive
format. The archive header adds a small amount of overhead, so very small or
high-entropy files can still produce an output slightly larger than the input.

## Archive format

The `.huf` file is binary and contains:

- An 8-byte `DSAHUF1` signature, format version, and mode.
- The original byte count and encoded bit count.
- In Huffman mode, a preorder tree (`0` means branch; `1` means leaf followed
  by its byte value).
- The compressed bit stream, packed most-significant bit first. The final
  byte is zero-padded when needed.

All multi-byte header integers use big-endian byte order. Decompression checks
the signature, version, tree, lengths, padding, truncation, and trailing data.

## Data structures and complexity

- **Min-heap:** selects the two lowest-frequency tree nodes while building the
  Huffman tree.
- **Binary tree:** maps variable-length codes back to byte values.
- **Frequency table:** counts the 256 possible byte values.

For an input of `n` bytes and `k` distinct byte values, tree construction is
`O(k log k)`, and encoding or decoding is `O(n × code length)` in this
straightforward teaching implementation. The input is held in memory while
compressing, so this version is intended for coursework and ordinary text
files rather than very large archives.

## Project files

- `huffman_compressor.py` — compressor, decompressor, and command-line entry
  point.
- `README.md` — usage, algorithm, and file format notes.
