"""Keep validated local line guides without reusing anyone's word timestamps."""
import re
from difflib import SequenceMatcher

STAMP = re.compile(r"^\s*\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
WORD = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
META = re.compile(r"^\s*\[[a-zA-Z]{2,}:[^\]]*\]\s*$")
OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$", re.I)
SECTION = r"(?:intro|outro|verse|chorus|hook|bridge|pre-?chorus|post-?chorus|refrain|interlude|instrumental)"
HEADING = re.compile(r"^\s*(\[[^\]]*\]|\(\s*"+SECTION+r"\b[^()]{0,30}\)|"+SECTION+r"\b[^():]{0,12}:)\s*$", re.I)


def words(text):
    parsed,_=rows(text)
    if parsed:return "\n".join(line for _,line in parsed)
    out=[]
    for line in (text or "").lstrip("\ufeff").splitlines():
        if META.match(line):continue
        while (match:=STAMP.match(line)):
            line=line[match.end():]
        line=WORD.sub("",line).strip()
        if line and not HEADING.match(line):out.append(line)
    return "\n".join(out)


def rows(text, duration=0):
    """All sung lines must have valid guides. Mixed/broken guides are never guessed."""
    result=[]; offsets=[]; repeated=False
    for line in (text or "").lstrip("\ufeff").splitlines():
        if (offset:=OFFSET.match(line)):
            offsets.append(int(offset[1])/1000);continue
        if META.match(line) or not line.strip():continue
        stamps=[]
        while (match:=STAMP.match(line)):
            if int(match[2])>=60:return [],"line timestamps contain invalid seconds"
            fraction=match[3] or "0"
            stamps.append(int(match[1])*60+int(match[2])+int(fraction)/10**len(fraction))
            line=line[match.end():]
        line=WORD.sub("",line).strip()
        if not line or HEADING.match(line):continue
        if not stamps:return [],"some lyric lines have no line timestamp"
        repeated=repeated or len(stamps)>1
        result.extend((stamp,line) for stamp in stamps)
    if not result:return [],"no usable line timestamps"
    if len(set(offsets))>1:return [],"conflicting LRC offsets"
    offset=offsets[0] if offsets else 0
    result=[(stamp+offset,line) for stamp,line in result]
    if repeated:result.sort(key=lambda row:row[0])
    if any(t<0 or (duration>0 and t>duration+1) for t,_ in result):return [],"line timestamps fall outside this recording"
    if any(b[0]<a[0] for a,b in zip(result,result[1:])):return [],"line timestamps run backwards"
    return result,""


def render(rows):
    def stamp(t):
        ms=round(t*1000)
        return "[%02d:%06.3f]" % (ms//60000,(ms%60000)/1000)
    return "\n".join(stamp(t)+line for t,line in rows)


def remap(text, reference, duration=0):
    """Reuse guides only for the same lines or a small edit, never provider words."""
    old,_=rows(reference,duration);new=words(text).splitlines()
    if not old or len(new)!=len(old):return ""
    key=lambda line:" ".join(line.casefold().split())
    matcher=SequenceMatcher(None,[key(line) for _,line in old],[key(line) for line in new],autojunk=False)
    matches=sum(block.size for block in matcher.get_matching_blocks())
    if matches<len(old) and (matches<3 or matches/len(old)<.90):return ""
    for tag,a,b,c,d in matcher.get_opcodes():
        if tag!="equal" and (tag!="replace" or b-a!=d-c):return ""
    return render([(old[i][0],line) for i,line in enumerate(new)])


def prepare(text, reference="", duration=0, unanchored=False):
    if unanchored:return words(text),False,"Line guides explicitly discarded; aligning from scratch."
    parsed,reason=rows(text,duration)
    if parsed:return render(parsed),True,"Supplied line guides retained; word timestamps recalculated from audio."
    mapped=remap(text,reference,duration) if reference else ""
    if mapped:return mapped,True,"Existing line guides retained for unchanged lines and small lyric edits; word timestamps recalculated."
    has_stamp=any(STAMP.match(line) for line in (text or "").splitlines())
    note="Line guides ignored: "+reason+". " if has_stamp else ""
    return words(text),False,note+"No compatible line guides; aligning untimed words from scratch."
