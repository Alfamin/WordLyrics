# WordLyrics

Word-by-word timed lyrics for a local music folder. Point it at the folder, let it run, read the report.

For every song it writes a `<song>.elrc` file next to the song (Enhanced LRC: each word has its own start
and end time). Players that support word-level lyrics, such as Noctis, pick that file up by themselves.

Your songs are only read. Every run makes a backup first; selected-song runs back up only those songs
and their sidecars. Normal runs add new lyrics. Confirmed reruns can replace an existing `.elrc` after
a verified backup and successful timing checks.

## Install

Nothing has to be installed first: no Python, no drivers, no administrator rights. You need 64-bit
Windows 10 (1903 or newer) or 11, an internet connection for the first start, and about
2.5 GB of free space.
A supported graphics card speeds up timing; the processor is used when the GPU is unavailable or fails.

**One line.** Open PowerShell (Start menu, type "PowerShell"), paste this, press Enter:

    irm https://raw.githubusercontent.com/Alfamin/WordLyrics/main/install.ps1 | iex

It puts the program into a `WordLyrics` folder in your user folder, adds a "WordLyrics" shortcut to the
desktop and starts it. Running the same line again later updates the program.

If Noctis is missing, the installer offers **1 Download and install**, **2 Choose an existing Noctis.exe**,
or **0 Continue with WordLyrics only**. The optional Windows installer comes from Noctis's official
GitHub release, is checked against its known size and SHA-256 fingerprint, and installs for your user.
Noctis download/setup failures are reported separately; they do not prevent WordLyrics installation.
Noctis 1.5.9 is the tested installer version. Older players are offered an update; newer installations
are not downgraded. The WordLyrics program also preserves a newer installed version, including a
private friend build newer than the public repository.

**Or by hand.** [Download the zip](https://github.com/Alfamin/WordLyrics/archive/refs/heads/main.zip),
unpack it anywhere, double-click `WordLyrics.bat`.
(Windows may ask whether to run a file from the internet: choose Run.)

The first start sets itself up, inside its own folder only: a portable Python (12 MB), the packages
(80 MB), and at the first run the two models (1.3 GB). That takes a few minutes and shows its progress.
Setup has backup servers and model downloads continue where supported. Python, pinned dependency
packages, model files and the optional Noctis installer are checked against known fingerprints.
To remove WordLyrics, delete its folder and the
shortcut. That is all there is.

## Use

1. Double-click **WordLyrics** (or drag your music folder onto it).
2. Choose **1 Generate missing timestamps**. One Noctis library folder is selected automatically.
   With several folders, choose one, several by number, or **All folders**. This includes Telegram
   download folders. You can choose another folder with **4 Choose music folders**.
3. Generation starts after folder selection; there is no second hidden Start prompt. Leave it running.
   The result counts actual word-timed songs and songs that need attention. **No new word timestamps
   were written** is shown when none succeeded. Failures never pause the batch for a lyric page.

An existing `.elrc` containing only plain/line lyrics is offered for **Repair after verified backup**.
This replacement has a prominent confirmation; ordinary new-file generation starts directly.
Player-specific `.lyricsfile` and `.ttml` files are reported as protected, with word timing unverified,
rather than being counted as confirmed word timestamps.

The main menu keeps Generate, Songs, Retry, Folders, Advanced, Connection checks, Resume/fix jobs and Search.
Prominent **WORDLYRICS INPUT** prompts separate app choices from the shell prompt. Advanced holds
selected-song reruns, source choices, full retiming, speed/offline options, setup and reports.
Overlapping folders are deduplicated, and each selected folder keeps its own reports and backup history.

The menu offers generation, lyric-status views, missing/failed retries, selected-song reruns, optional
source choices, a complete-library rerun, reports and restoration, settings, and installation checks.
Lists show 25 songs per page, with search and stable numbered selections such as `1,3-5`. You can also
drop selected audio files into the rerun menu. Persian and Arabic number keys work too.

For a rerun, **Keep words and redo timing** retains valid line guides and recalculates every word
from the audio. It keeps your exact words and ignores saved provider choices. The advanced menu also
offers locating the current words entirely from scratch (useful for badly delayed line guides), or
fetching fresh lyrics from the providers. Replacements require a byte-verified original backup;
the accepted candidate and recovery record are saved before replacing the `.elrc`. A rejected result
keeps the original. A whole-library rerun asks for confirmation twice and can take hours.
Ordinary generation and timing-only reruns protect `.ttml` and `.lyricsfile` files because those formats
take priority in players. **Wrong lyrics / redo with fresh lyrics** explicitly handles those files too.
After accepted word timing is ready, their verified originals become inactive `.bak` files beside the
song, with a recovery journal and independent backup. Failed publication restores the originals;
files changed by another writer are never replaced. **Reports → 4 Restore archived incorrect lyric
files** restores them without overwriting later edits. Audio and embedded tags are never changed.

Choose **8 Search / fix a song**, or type part of a title directly in the main menu. Search covers
title, artist, album and filename, across selected folders. Results show numbered matches and paths;
close spelling suggestions are labelled. Select a song, then fresh automatic lookup, keep words and
retime, choose Genius / LRCLIB / a text file, or edit/paste custom lyrics. The text editor works on a
private draft; correcting a line does not modify the active lyrics until an approved timing run succeeds.
Custom synced LRC/ELRC inputs and manually chosen LRCLIB results retain valid **line** timestamps as
rough guides; old word timestamps are always recalculated by the model. The app and terminal editors
keep the visible line guides. A small plain-text correction can reuse existing guides only when line
order/count match and at least 90% of lines remain unchanged. A larger rewrite starts from scratch.
Malformed, backwards, conflicting-offset, mixed or out-of-recording guides are ignored with a reason
in the report. Repeated LRC chorus timestamps expand in chronological order, and global offsets apply.
If a guided alignment fails, the same words get one strict unanchored attempt using the same hearing;
there is no extra GPU model pass. Incorrect online lyrics still fail the recording checks.
After you choose or edit the words, timing uses them locally without another online lyric lookup.
Genius pages still pass the recording/audio gates; automatic runs never wait for a manual source choice.

In Noctis, right-click a song → **Wrong lyrics / redo with fresh lyrics (WordLyrics)** opens this song's
repair choices. **Edit / paste custom lyrics (WordLyrics)** opens an editable window; current LRC/text
words load when available. Embedded words can be imported through the terminal editor; TTML/Lyricsfile
text is not imported into the app editor, so paste the full lyrics if necessary. The timing window shows
one final backup/replacement confirmation. After success, select another song and return to reload the
new lyrics in Noctis; its plugin API has no lyric reload hook. An old saved Genius page is ignored for a
fresh automatic repair. Explicit source/custom choices remain available and unfinished jobs resume
only their selected songs.

Menu **5 Advanced → 4 Reports → 2** restores previous timing and archives the newer file. Restoration refuses to replace
a lyric file edited since the rerun or to use a damaged backup. **Undo new additions** handles newly
created sidecars separately.

From a terminal:

    WordLyrics.bat menu "D:\Music"                    open the numbered menu for this library
    WordLyrics.bat "D:\Music"                         open the menu in an interactive terminal
    WordLyrics.bat "D:\Music" --yes                   run without any question
    WordLyrics.bat "D:\Music" --backup-to "E:\Backups"
    WordLyrics.bat "D:\Music" --dry-run               only look: what is there, what would be done
    WordLyrics.bat "D:\Music" --offline               never go online for lyrics
    WordLyrics.bat "D:\Music\Artist - Song.flac"      open the menu with this song available for selection
    WordLyrics.bat undo "D:\Music"                    take out again what WordLyrics wrote
    WordLyrics.bat check                              is everything installed, how fast is this computer
    WordLyrics.bat setup                              fetch the models now instead of at the first run
    WordLyrics.bat providers                          show which lyric sources are enabled
    WordLyrics.bat diagnose                           connection checks and a shareable diagnostic file
    WordLyrics.bat repair-song "D:\Music\Artist - Song.flac"  repair/edit/source choices for just this song
    WordLyrics.bat source "D:\Music\Artist - Song.flac" "https://genius.com/Artist-song-lyrics"

Other switches: `--speed full|half|light`, `--retry`, `--no-lrc`, `--quick-backup`, `--only TEXT`,
`--limit N`, `--no-open`, `--plain-output`, and for other programs `--songs-from FILE` (a list of songs,
one path per line) and `--result FILE` (what happened to each song, as JSON).
`--progress FILE` writes live stages/counts for the Noctis plugin. Exit status **4** means the run ended
with songs that still need word timing; **0** means no incomplete outcomes, **3** means another run is
busy, and **130** means canceled. A skipped existing lyric file is explained in the report.
`--retry` also asks the lyric sources again instead of reusing cached answers.
Automation and the Noctis background worker continue to use `--yes`: no menu or per-song questions.
Advanced selected-song reruns use `--redo --retime-mode current|guided|unanchored|fresh --yes`; `current`
and `guided` keep valid line guides, while `unanchored` explicitly discards them. A selected-song
list can use `--songs-from`. A whole-folder `--redo` additionally requires `--confirm-redo`, which
the numbered menu supplies only after both confirmations. `--lyrics-file FILE` accepts a UTF-8
lyric text file for exactly one selected song.

## With the Noctis player: new songs get their lyrics by themselves

A small plugin for [Noctis](https://github.com/heartached/Noctis) (1.5.5 or newer, Windows) watches your
library folders. When a song appears there (downloaded, copied, ripped, however it got there) the plugin
waits until the file has been left alone (10 seconds for a new file, 2 seconds after a final rename),
then has WordLyrics time the new songs in the background. A small **WordLyrics progress** window shows
the current stage, measured stage progress, song count and errors. Timing speed depends on the song
and computer. Closing the progress window keeps work running.

If Noctis is on the computer when you install WordLyrics with the one line above, the plugin is put into
Noctis by the installer. All that is left is to switch it on:

1. In Noctis: Settings → Plugins → turn on **Community plugins**, then switch **WordLyrics** on and
   approve what it asks for (a track menu entry, notices, internet). If it is not in the list yet,
   restart Noctis or press the reload button there.

Noctis keeps every new plugin switched off until you do this; the installer does not touch Noctis'
settings.

The same line also looks after three other Noctis plugins, each from its own repository:
[Lyric Motion](https://github.com/Alfamin/LyricMotion) (animated lyrics) and
[Free Music Finder](https://github.com/Alfamin/noctic-download-plugin) (finds and downloads songs), plus
[True Shuffle](https://github.com/Alfamin/TrueShuffle) (equal-chance, no-repeat and discovery shuffle modes;
Noctis 1.5.9 or newer). They
are put into Noctis when they are missing and updated when a newer version is out; a plugin that is
already up to date, or newer than the published one, is left alone. Their logins and settings are kept.
Close Noctis before running the line, since Noctis holds its plugins' files while it is open. To leave
the three out, set `$env:WORDLYRICS_NO_EXTRAS = '1'` first.

True Shuffle adds a mode menu to Noctis's shuffle button. Choose **No repeats** for remembered cycles,
**Discovery** for less-played songs first, or **Never played only**. The menu also offers playlist and
whole-library sources and Noctis's normal shuffle. Installation does not enable it or change playback;
approve and enable True Shuffle in Noctis first.

The other way round (the plugin first): in Noctis choose **Install from file…** with
`noctis-plugin\WordLyrics-for-Noctis.zip` from the WordLyrics folder
(or [download it](https://github.com/Alfamin/WordLyrics/raw/main/noctis-plugin/WordLyrics-for-Noctis.zip)),
switch it on, then flip **Install or update WordLyrics** in the plugin's settings: a window opens,
installs WordLyrics and fetches the models.

What else it does:

- Right-click any song → **Time the words (WordLyrics)**, for songs that were already there.
- Songs that arrived while Noctis was closed are picked up at its next start.
- It works with whatever brings the songs in, download plugins included: nothing has to be set up
  between them, because the plugin only looks at the library folders.
- It stops with Noctis. Closing Noctis in the middle stops the work; the songs not reached wait for
  the next start. If WordLyrics is busy in its own window, the plugin waits for it.
- Its settings: on/off, how hard the computer works (half by default), notices, the backup folder, the
  WordLyrics folder, **Generate timestamps / choose music folders**, **Open progress and last result**,
  and whether progress opens automatically. Missing setup offers a direct **Set up WordLyrics** button.

## When something does not work

**Progress is saved, including when access fails before songs can be scanned.** Choose
**7 Resume / fix unfinished jobs**, or **Fix problem / resume affected songs** in the Noctis progress
window. It shows the remaining songs and reasons, and offers Retry, permission repair, different
lyrics, a different backup folder, or connection checks. Completed word timestamps are kept; retries
use only affected songs and newly accessible parts of the originally selected scope.

Permission repair shows its exact targets and asks for **Windows UAC administrator approval**.
Only a fixed helper is elevated; the lyric model keeps running as the original user. The rule is for
that user even if a different administrator approves. Audio files receive read access; selected music
folders receive Modify for sidecar creation. Ownership and other users' rules stay in place. A read-only
`.elrc` can be unlocked for an explicitly requested repair. Successful fixes continue the saved scope;
canceling UAC keeps the job saved. The helper refuses system directories, whole profiles, drive roots,
linked paths and remote shares; it does not remove deny rules or grant Everyone/FullControl.

Recovery is bounded: providers/mirrors are tried in order, recent positive cached provider data may
be used during connection failure (the usual matching/censorship/audio checks still apply), GPU failures
fall back to CPU, and damaged private packages get one repair attempt. Failed model loading triggers
hash checks; only missing/damaged known models are restored, preserving damaged copies. Global Python,
security policies, antivirus settings and drivers are not modified. A valid model that this hardware
cannot load remains a clearly reported blocker; incorrect word timestamps are never invented.

Choose **6 Check connections & setup**. It tests LRCLIB and enabled Genius separately and writes
`diagnostics.json` in the app folder. That file contains versions, model readiness and provider codes,
without credentials, lyrics, phone numbers or music paths. Send that file when asking for help.

The run report lists each song's reason: missing artist/title, unreadable audio, no matching lyrics,
censored sources rejected, timing quality too low, existing files protected, or a provider failure.
Connection failures use codes such as `DNS_FAILED`, `TIMEOUT`, `TLS_FAILED`, `HTTP_403`, `RATE_LIMIT`
and `SERVER_ERROR`. A blocked provider is stopped for that batch instead of repeatedly waiting on it;
other sources and existing usable lyrics can still work. **Retry** checks again on a new run.
The models load only when a song has usable lyrics ready for timing; no-lyric runs avoid that download/load.
Noctis's last result and startup output are kept in its WordLyrics plugin-data folder.

Tests simulate Windows 10/11 version boundaries, standard users, a different approving administrator,
denied/read-only/disconnected targets, canceled or tampered permission requests, failed GPU/CPU tests,
broken packages/models, stale caches and interrupted jobs. CI is configured for Windows Server 2022
and 2025 runners. These are not a claim of actual desktop Windows 10/11 VM coverage or guaranteed
compatibility with every computer.

## Private setup for friends

The public program and plugin contain no shared Telegram API hash. A private deployment can supply
`telegram-defaults.private.json` through `WORDLYRICS_TELEGRAM_DEFAULTS_FILE`. The installer validates
it and puts it in Noctis's Free Music Finder **plugin-data** folder. Existing private defaults and
saved logins are preserved. Free Music Finder 2.4.0 or newer then starts with phone-number login;
**Advanced: Telegram API settings** lets each person override the defaults. Rejected credentials
have a specific error and instructions. Each person still supplies their own Telegram code and,
when enabled, their two-step password. No account session is shared.

A private friend bundle may also supply local program/Finder ZIPs via `WORDLYRICS_PACKAGE` and
`WORDLYRICS_FINDER_PACKAGE`, so it uses the supplied versions before those versions are published.
`WORDLYRICS_EXTRAS_DIR` supplies other local ZIPs named by plugin id; the friend bundle can include
all four plugins and avoid GitHub downloads for the program/plugins themselves.
Keep bundles/configuration with real defaults out of public repositories and public download links.
For unattended installs, set `WORDLYRICS_NONINTERACTIVE=1` to skip the optional Noctis question.
`WORDLYRICS_INSTALL_NOCTIS=1` explicitly requests the tested Noctis installer without prompting.

Repeated setup verifies a local program-file fingerprint record and reuses the current/newer program
instead of downloading its ZIP. Installed optional plugins check a small version manifest first;
their ZIPs download only for an update or a missing/damaged installed file. An unavailable update
check keeps working plugins and reports that the check failed. Plugins installed through this
installer have dependency-file fingerprints too; manually installed plugins at least check their
entry DLL. Existing packages are checked locally against pinned versions and usable imports, and
correct model files are reused. Noctis's setup action reuses a complete local program.
`WORDLYRICS_REPAIR_INSTALL=1` forces same-version repair downloads. `WORDLYRICS_OFFLINE_INSTALL=1`
uses installed/supplied program/plugin files without network checks or automatic startup downloads.
Run the saved `install.ps1` or private bundle locally for offline setup: fetching the public one-line
installer itself still requires internet. A first model/Python install and online lyric searches need
network access unless their required files were already supplied. Offline mode cannot recreate files
that are absent from both the computer and supplied packages.

The plugin does no lyric work itself and never touches a song: it starts WordLyrics on exactly the new
songs. Everything said on this page about safety holds; of a new song only that song and the lyric files
next to it are copied to the backup, into a folder named `<music folder> <date> (single songs)`.
Its source is in `noctis-plugin\src`; `noctis-plugin\build.ps1` builds it without the .NET SDK.

## What it does, in order

1. **Backup copy.** The whole folder is copied (every file, not only songs), and every copied file is read
   back and compared. By default the copy goes to `WordLyrics Backups` on the drive with the most free
   space. Each run gets its own complete backup folder; files that did not change since the previous
   backup are shared with it instead of being copied again, so later backups take seconds.
   If the backup cannot be completed, nothing else happens.
   (Given single songs instead of a folder, only those songs and their lyric files are copied.)
2. **Read your songs.** Which lyrics does each song already have: a `.lrc` file, lyrics inside the song
   file, a `<song>.txt` you put next to it, or nothing. Songs that already have word-level lyrics are
   left alone.
3. **Find lyrics** (only for songs that need it): [LRCLIB](https://lrclib.net) first, then
   [Genius](https://genius.com) as the fallback.
   Automatic searches require matching title, artist and recording version. Where the source provides duration, it must differ
   by at most 2.5 seconds. Genius matches without duration must be confirmed against the actual audio.
4. **Time the words.** The song is listened to by two models: one isolates the voice, one hears which
   letters are sung when. The whole song is matched against the lyrics; untimed lyrics receive a
   coarse alignment followed by a guarded refinement audit.
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

## Lyrics sources and explicit recordings

LRCLIB remains the default. If its lyrics are missing, visibly censored, or fail the model's recording
or timing checks, the next source is tried. The models listen once: trying another source reuses that
audio analysis. Reports name the source that was actually used.

Uncensored lyrics are preferred by default. Clean versions and words masked with asterisks, bullets,
or bleep markers are skipped for explicit or unlabelled recordings. Known clean recordings keep their
version. Explicit advisory tags are read when available; they are never written. Missing words are
never guessed. Censorship without visible markers or reliable version metadata can still escape detection.
Normal runs skip existing word-timed files. Explicit, confirmed reruns may replace `.elrc` files
after the backup, audio-match and timing checks. The explicitly selected wrong-lyrics repair can
archive higher-priority formats after accepted timing; ordinary runs keep those files protected.

Genius uses public search and lyric pages and needs no key for that route. Its artist/section headings
are removed, and the remaining words are aligned to your audio. This helps with underground and
unreleased songs that have no database timestamps. A file's `(unreleased)` or `(leak)` label may be
absent from the Genius title; remix, live, snippet, clean and other recording variants still must match.
If the site blocks access or changes its lyric containers, the result is refused and reported.
WordLyrics uses public pages and does not request or use API keys.

Genius supplies **plain words** to WordLyrics: any supplied timestamps are discarded, and our model
calculates new word times. No shared keys or browser sessions are used.

For untimed lyrics, a second stage builds broad, soft phrase guides from words heard clearly in the
first alignment. Only supported changes are kept: it preserves lyric text, reliable timing anchors,
held notes, coverage, and the normal acceptance checks. An alignment with no useful outlier avoids
a redundant calculation. This stage reuses the same model outputs and adds no GPU listening pass.
On 39 reserved human-reference benchmark songs it retained the first-pass timings throughout; it
has not demonstrated a measurable word-start accuracy gain. The existing onset and word-end checks
still run afterward. Difficult songs can still be refused rather than given unreliable word times.

To disable the fallback, copy `lyrics-providers.example.json` to `lyrics-providers.json` in the
WordLyrics folder and set `genius` to `false`. Run `WordLyrics.bat providers` to see source status.
`--offline` disables every online source.

Automatic search always finishes or records a failure without waiting for input. Afterward, menu
**5** lets you optionally choose a Genius page or your own lyric text file, or reset to automatic
search. Reports include a Genius search link for unresolved songs. You can also use the `source`
command above with the exact Genius page you have checked. It creates a new
`<song>.lyrics-source.txt` beside that song. The chosen page takes priority over database searches;
tracking parameters are discarded. Its words must still pass the audio and timing checks, and a
failed choice does not silently switch to another lyric. Edit that small text file yourself if you
want a different page, or update/reset it in menu **5** (the previous choice is backed up).
Normal runs still skip existing word timing; use the rerun menu to replace it. `--offline` never fetches the page.
The command adds only the source-choice file; it does not edit audio or replace current lyrics.

Audio formats: MP3, FLAC, M4A/MP4 (AAC and Apple Lossless), WAV, AIFF, OGG Vorbis, Opus, WMA, WavPack,
bare AAC, APE. All of these except APE were tested from start to finish.

## What it will not do

- It never changes, moves, renames or deletes a song. Audio and tags are only read.
- Normal runs create lyric files in "new file only" mode. Confirmed reruns replace only existing
  `.elrc` timing after its verified backup and the successful result are saved. Source-choice updates
  also preserve their old file. Failed checks preserve the original.
- New lyric files are written and checked under a unique temporary name before they become visible
  as lyrics. A failed write leaves no partial `.elrc` to block a later retry; a racing writer keeps its
  own file. Only WordLyrics' own temporary file can be cleaned up.
- It never writes lyrics it is not sure about: lyrics from the internet are also checked against the audio
  (right lyrics fit far better in their real order than with their lines shuffled; lyrics of another song
  do not), and a line the model cannot hear stays an ordinary line instead of getting made-up word times.
- It never runs more than one listener at a time, and a second WordLyrics window refuses to start a
  second run. Running several was measured to be slower on a strong graphics card and to run out of
  memory on a weaker one. Instead, helper threads prepare the next piece of work while the models are busy.

## Speed

Word endings receive a conservative final check using the already available voice and alignment data.
It can shorten a fading ending by 0.08–0.25 seconds, while keeping word starts, line stamps,
joined words, and the confidence gate unchanged. On 39 reserved Jamendo benchmark songs, line-final
ends within 0.30 seconds improved from 84.6% to 87.7%; individual endings can still be wrong.
This adds no extra model pass. Existing lyrics are skipped unless you explicitly choose a rerun.

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
- `runs\<music folder>\lyrics service cache\` : cached provider answers (LRCLIB for up to seven days;
  Genius for up to one day)
- `lyrics-providers.json` : optional local provider settings; preserved during updates and excluded from Git
- `menu-settings.json`, `selections\`, `source-choice backups\` : local menu preferences, selected-song
  lists and previous manual source choices; excluded from Git
- `runs\<music folder>\run <date>\retimed.jsonl` and `retiming candidates\` : original backup location,
  fingerprints and accepted replacement candidates; restoration archives newer timing under `restores\`
- `models\` : the two model files
- `.python\` : the portable Python and its packages
- `noctis-plugin\` : the plugin for the Noctis player

## What is downloaded, and from where

| What | Normal source | Backup sources |
|---|---|---|
| Portable Python 3.14 (12 MB) | python.org | npmmirror.com, huaweicloud.com |
| Python packages (80 MB) | pypi.org | runflare.com, tsinghua.edu.cn, aliyun.com |
| Voice isolation model (67 MB) | github.com | huggingface.co (two copies) |
| Word aligner model (1.26 GB) | huggingface.co | huggingface.co (second copy) |
| Lyrics, per song | lrclib.net | genius.com |

Runtime/model download sources were checked from test servers inside Iran (October 2026); Genius
public fetching was checked on the development PC. If none of the download sources
for something works where you are, add an address that does to `extra-links.txt`, or put the two model
files into the `models` folder by hand.

## What is sent over the internet

Only when lyrics are looked up: artist, title, album and duration as needed by the selected provider.
The audio is never uploaded. No API keys or browser credentials are used.
Nothing is sent to lyric services with `--offline`.

Provider regression tests use synthetic lyrics and fake API responses:
`python -m unittest discover -s tests -v`.
GitHub Actions runs these tests on Windows with Python 3.14 for pushes and pull requests. It uses
hash-verified lightweight test dependencies, read-only repository permissions, and pinned action
commits. It downloads no audio models and performs no live lyric searches.

## Models and licences

- Word aligner: MMS-300m forced aligner (Meta), ONNX export. Licence CC-BY-NC 4.0: personal,
  non-commercial use.
- Voice isolation: MDX-Net "Kim Vocal 2", from the Ultimate Vocal Remover model collection.

The word times are worked out from the audio by a model. They are not supplied by a lyrics source.
