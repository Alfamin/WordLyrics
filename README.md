# WordLyrics

Word-by-word timed lyrics for a local music folder. Point it at the folder, let it run, read the report.

For every song it writes a `<song>.elrc` file next to the song (Enhanced LRC: each word has its own start
and end time). Players that support word-level lyrics, such as Noctis, pick that file up by themselves.

Your songs are only read. A full backup copy of the folder is made before anything else happens.

## Install

Nothing has to be installed first: no Python, no drivers, no administrator rights. You need 64-bit
Windows 10 or 11, a graphics card (any brand), an internet connection for the first start, and about
2.5 GB of free space.

**One line.** Open PowerShell (Start menu, type "PowerShell"), paste this, press Enter:

    irm https://raw.githubusercontent.com/Alfamin/WordLyrics/main/install.ps1 | iex

It puts the program into a `WordLyrics` folder in your user folder, adds a "WordLyrics" shortcut to the
desktop and starts it. Running the same line again later updates the program.

**Or by hand.** [Download the zip](https://github.com/Alfamin/WordLyrics/archive/refs/heads/main.zip),
unpack it anywhere, double-click `WordLyrics.bat`.
(Windows may ask whether to run a file from the internet: choose Run.)

The first start sets itself up, inside its own folder only: a portable Python (12 MB), the packages
(80 MB), and at the first run the two models (1.3 GB). That takes a few minutes and shows its progress.
Every download has backup servers and continues where it stopped if the connection breaks; every file is
checked against a known fingerprint before it is used. To remove WordLyrics, delete its folder and the
shortcut. That is all there is.

## Use

1. Double-click **WordLyrics** (or drag your music folder onto it).
2. It asks for the music folder, shows where the backup copy will go, and asks how hard the computer
   should work. Press Enter for the defaults.
3. Leave it. When it is done, the report opens.

From a terminal:

    WordLyrics.bat "D:\Music"                         run, asking only where the backup goes and how fast
    WordLyrics.bat "D:\Music" --yes                   run without any question
    WordLyrics.bat "D:\Music" --backup-to "E:\Backups"
    WordLyrics.bat "D:\Music" --dry-run               only look: what is there, what would be done
    WordLyrics.bat "D:\Music" --offline               never go online for lyrics
    WordLyrics.bat undo "D:\Music"                    take out again what WordLyrics wrote
    WordLyrics.bat check                              is everything installed, how fast is this computer

Other switches: `--speed full|half|light`, `--retry`, `--no-lrc`, `--quick-backup`, `--only TEXT`,
`--limit N`, `--no-open`, `--plain-output`.

## What it does, in order

1. **Backup copy.** The whole folder is copied (every file, not only songs), and every copied file is read
   back and compared. By default the copy goes to `WordLyrics Backups` on the drive with the most free
   space. Each run gets its own complete backup folder; files that did not change since the previous
   backup are shared with it instead of being copied again, so later backups take seconds.
   If the backup cannot be completed, nothing else happens.
2. **Read your songs.** Which lyrics does each song already have: a `.lrc` file, lyrics inside the song
   file, a `<song>.txt` you put next to it, or nothing. Songs that already have word-level lyrics are
   left alone.
3. **Find lyrics** (only for songs that need it), from [LRCLIB](https://lrclib.net), a free public lyrics
   database. A result is used only when it is clearly the same recording: title and artist match, same
   version, and the length differs by 2.5 seconds at most.
4. **Time the words.** The song is listened to by two models: one isolates the voice, one hears which
   letters are sung when. The whole song is then matched against the lyrics in one pass.
5. **Final check.** The folder is compared with how it was before the run.

Steps 3 and 4 run side by side: songs that already have lyrics are timed while lyrics for the others are
being looked up.

## What each song gets

| The song has | What happens |
|---|---|
| Word-level lyrics already (`.elrc`, `.ttml`, `.lyricsfile`, or word tags inside) | Left alone |
| Line-timed lyrics (`.lrc` file or inside the song file) | Words are timed inside those lines; `.elrc` written |
| Plain lyrics (no times) | Line-timed lyrics with the same words are looked up online; otherwise the model places the lines itself and the result is used only if it is sure of nearly all of them |
| No lyrics | Lyrics are looked up online, then as above; a line-timed `.lrc` is written too, for players that cannot read `.elrc` |
| No lyrics, none found | Nothing is written; listed in the report. Put the lyrics next to the song as `<song>.txt` and run again |

Audio formats: MP3, FLAC, M4A/MP4 (AAC and Apple Lossless), WAV, AIFF, OGG Vorbis, Opus, WMA, WavPack,
bare AAC, APE. All of these except APE were tested from start to finish.

## What it will not do

- It never changes, moves, renames or deletes a song or any other existing file. Songs are only read.
- It never overwrites a file. Lyric files are created in "new file only" mode.
- It never writes lyrics it is not sure about: lyrics from the internet are also checked against the audio
  (right lyrics fit far better in their real order than with their lines shuffled; lyrics of another song
  do not), and a line the model cannot hear stays an ordinary line instead of getting made-up word times.
- It never runs more than one listener at a time, and a second WordLyrics window refuses to start a
  second run. Running several was measured to be slower on a strong graphics card and to run out of
  memory on a weaker one. Instead, helper threads prepare the next piece of work while the models are busy.

## Speed

It runs on the graphics card (any DirectX 12 card: AMD, NVIDIA, Intel). Measured on a Radeon RX 7900 XT:
about 11 songs a minute at full speed. The computer is kept awake during a run. A run can be stopped
with Ctrl+C (or by closing the window) and started again later: finished songs are skipped.

You choose how hard it works, as a share of what your own computer can do:

- **Full** : fastest; best when you are away or asleep.
- **Half** : the models rest as long as they work; the computer stays comfortable to use.
- **Light** : about a quarter; for gaming or other heavy use at the same time.

It asks at the start (or use `--speed`), and you can switch during the run with the keys **1**, **2**,
**3**, and pause with **P** (the same keys work on a Persian or Arabic keyboard layout). Because the rest
is measured against how long each step takes on your machine, "half" means half of your graphics card,
whatever card that is.

## Where things are

Everything stays inside the WordLyrics folder:

- `runs\<music folder>\run <date>\report.html` : the report (also `songs.csv`, `summary.txt`, `log.jsonl`)
- `runs\<music folder>\all files written.csv` : every file ever written into that music folder
- `runs\<music folder>\lyrics service cache\` : answers from LRCLIB, so nothing is asked twice
- `models\` : the two model files
- `.python\` : the portable Python and its packages

## What is downloaded, and from where

| What | Normal source | Backup sources |
|---|---|---|
| Portable Python 3.14 (12 MB) | python.org | npmmirror.com, huaweicloud.com |
| Python packages (80 MB) | pypi.org | runflare.com, tsinghua.edu.cn, aliyun.com |
| Voice isolation model (67 MB) | github.com | huggingface.co (two copies) |
| Word aligner model (1.26 GB) | huggingface.co | huggingface.co (second copy) |
| Lyrics, per song | lrclib.net | none |

All of these were checked to open from test servers inside Iran (October 2026). If none of the sources
for something works where you are, add an address that does to `extra-links.txt`, or put the two model
files into the `models` folder by hand.

## What is sent over the internet

Only when lyrics are looked up: the artist, title, album and length of the song, to lrclib.net.
Nothing else, and nothing at all with `--offline`.

## Models and licences

- Word aligner: MMS-300m forced aligner (Meta), ONNX export. Licence CC-BY-NC 4.0: personal,
  non-commercial use.
- Voice isolation: MDX-Net "Kim Vocal 2", from the Ultimate Vocal Remover model collection.

The word times are worked out from the audio by a model. They are not supplied by a lyrics source.
