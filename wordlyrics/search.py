"""Local song lookup: partial metadata/filenames first, cautious typo suggestions second."""
from difflib import SequenceMatcher
import re
import unicodedata


def normal(text):
    value=unicodedata.normalize("NFKD",text.casefold())
    value="".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+",value,flags=re.UNICODE))


def find(songs,query):
    query=normal(query[:256])
    if not query:return list(songs),False
    tokens=query.split()
    direct=[];suggested=[]
    for s in songs:
        title=normal(s.title or s.name)
        artist=normal(s.artist)
        fields=normal(" ".join((s.title,s.artist,s.album,s.rel)))
        tie=(s.rel.casefold(),s.path.casefold())
        if all(token in fields for token in tokens):
            rank=0 if query==title else 1 if query in title else 2 if query in artist else 3
            direct.append((rank,tie,s))
        elif all(len(token)>=4 for token in tokens):
            words=fields.split()
            score=min((max((SequenceMatcher(None,t,w).ratio() for w in words),default=0) for t in tokens),default=0)
            if score>=0.78:suggested.append((-score,tie,s))
    if direct:return [row[2] for row in sorted(direct)],False
    return [row[2] for row in sorted(suggested)[:25]],bool(suggested)
