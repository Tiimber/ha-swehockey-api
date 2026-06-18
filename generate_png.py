"""generate_png.py – Render a 32x32 PNG scoreboard for a hockey team.

Layout (8 rows each):
  0– 7  Home team strip  – abbr left, score right
  8–15  Away team strip  – abbr left, score right
 16–23  Clock / countdown / next-match date
 24–31  Period dots: P1 P2 P3 OT SO
"""
from __future__ import annotations
import io, re, unicodedata
from datetime import datetime, timezone
from typing import Optional
from PIL import Image

# ── Team colors ──────────────────────────────────────────────────────────────
_TC: dict[str, tuple] = {
    "brynas_if":("#2a2a2a","#ffffff","#fecc03"), "brynas":("#2a2a2a","#ffffff","#fecc03"),
    "djurgardens_if":("#fbea05","#db0d1a","#1261ab"), "djurgardens":("#fbea05","#db0d1a","#1261ab"),
    "farjestad_bk":("#ffffff","#008e4f","#e5b843"), "farjestad":("#ffffff","#008e4f","#e5b843"),
    "frolunda_hc":("#ffffff","#0e5840",None), "frolunda":("#ffffff","#0e5840",None), "hc_frolunda":("#ffffff","#0e5840",None),
    "hv71":("#052e59","#fdd410",None), "hv_71":("#052e59","#fdd410",None),
    "linkoping_hc":("#052d5c","#ffffff","#b10c21"), "linkoping":("#052d5c","#ffffff","#b10c21"), "lhc":("#052d5c","#ffffff","#b10c21"),
    "lulea_hf":("#2a2a2a","#d10c12","#fdcd01"), "lulea":("#2a2a2a","#d10c12","#fdcd01"),
    "malmo_redhawks":("#ffffff","#bb0b2e","#2a2a2a"), "if_malmo_redhawks":("#ffffff","#bb0b2e","#2a2a2a"), "malmo":("#ffffff","#bb0b2e","#2a2a2a"),
    "modo_hockey":("#cf1f2d","#ffffff","#2c5235"), "modo":("#cf1f2d","#ffffff","#2c5235"),
    "rogle_bk":("#ffffff","#067b35",None), "rogle":("#ffffff","#067b35",None),
    "skelleftea_aik":("#fbba14","#2a2a2a",None), "skelleftea":("#fbba14","#2a2a2a",None), "saik":("#fbba14","#2a2a2a",None),
    "timra_ik":("#e32f25","#ffffff","#104999"), "timra":("#e32f25","#ffffff","#104999"),
    "orebro_hk":("#e3032a","#ffffff",None), "orebro":("#e3032a","#ffffff",None),
    "vaxjo_lakers":("#FF6600","#052f5d",None), "vaxjo":("#FF6600","#052f5d",None),
    "aik":("#2a2a2a","#FFD700",None),
    "almtuna_is":("#CC0000","#FFD700","#ffffff"), "almtuna":("#CC0000","#FFD700","#ffffff"),
    "bik_karlskoga":("#0b2d74","#ffffff",None), "karlskoga":("#0b2d74","#ffffff",None),
    "if_bjorkloven":("#0b5640","#fdd003",None), "bjorkloven":("#0b5640","#fdd003",None),
    "ik_oskarshamn":("#0b2d74","#c10230","#ffffff"), "oskarshamn":("#0b2d74","#c10230","#ffffff"),
    "karlskrona_hk":("#003F7F","#FFFFFF",None), "karlskrona":("#003F7F","#FFFFFF",None),
    "kristianstad_ik":("#CC0000","#FFFFFF","#1C1C1C"), "kristianstad":("#CC0000","#FFFFFF","#1C1C1C"),
    "leksands_if":("#0d3579","#ffffff",None), "leksand":("#0d3579","#ffffff",None), "lif":("#0d3579","#ffffff",None),
    "mora_ik":("#e42313","#fcda00","#007d32"), "mora":("#e42313","#fcda00","#007d32"),
    "nybro_vikings_if":("#e73137","#2a2a2a","#fed68f"), "nybro":("#e73137","#2a2a2a","#fed68f"),
    "tingsryd_aif":("#CC0000","#FFFFFF",None), "tingsryd":("#CC0000","#FFFFFF",None),
    "vik_vasteras_hk":("#fdd200","#2a2a2a",None), "vasteras":("#fdd200","#2a2a2a",None),
    "vastervik_ik":("#007755","#FFFFFF",None), "vastervik":("#007755","#FFFFFF",None),
    "sodertalje_sk":("#1264b0","#fddf00","#d4b882"), "sodertalje":("#1264b0","#fddf00","#d4b882"),
    "huddinge_ik":("#CC0000","#FFFFFF",None), "huddinge":("#CC0000","#FFFFFF",None),
    "kalmar_hc":("#ac0e09","#ffffff","#f1da9e"), "kalmar":("#ac0e09","#ffffff","#f1da9e"),
    "troja_ljungby":("#dc2f34","#ffffff",None), "if_troja_ljungby":("#dc2f34","#ffffff",None), "troja":("#dc2f34","#ffffff",None),
    "vimmerby_hockey":("#fddd01","#2a2a2a","#ffffff"), "vimmerby":("#fddd01","#2a2a2a","#ffffff"),
    "ostersunds_ik":("#fded00","#006633",None), "ostersund":("#fded00","#006633",None),
}
_FALLBACK = ("#CC0000","#00AA00","#0000CC")

# ── Abbreviations ─────────────────────────────────────────────────────────────
_ABBR: dict[str, str] = {
    "brynas_if":"BIF","brynas":"BIF","djurgardens_if":"DIF","djurgardens":"DIF",
    "frolunda_hc":"FHC","frolunda":"FHC","hc_frolunda":"FHC",
    "farjestad_bk":"FBK","farjestad":"FBK",
    "hv71":"HV71","hv_71":"HV71",
    "leksands_if":"LIF","leksand":"LIF","lif":"LIF",
    "linkoping_hc":"LHC","linkoping":"LHC","lhc":"LHC",
    "lulea_hf":"LHF","lulea":"LHF",
    "malmo_redhawks":"MAL","if_malmo_redhawks":"MAL","malmo":"MAL",
    "rogle_bk":"RBK","rogle":"RBK",
    "skelleftea_aik":"SAIK","skelleftea":"SAIK","saik":"SAIK",
    "timra_ik":"TIK","timra":"TIK",
    "vaxjo_lakers":"VLH","vaxjo":"VLH",
    "orebro_hk":"OHK","orebro":"OHK",
    "aik":"AIK","almtuna_is":"AIS","almtuna":"AIS",
    "bik_karlskoga":"BIK","karlskoga":"BIK",
    "if_bjorkloven":"IFB","bjorkloven":"IFB",
    "ik_oskarshamn":"IKO","oskarshamn":"IKO",
    "modo_hockey":"MODO","modo":"MODO",
    "mora_ik":"MIK","mora":"MIK",
    "nybro_vikings_if":"NYB","nybro":"NYB",
    "troja_ljungby":"TRO","if_troja_ljungby":"TRO","troja":"TRO",
    "sodertalje_sk":"SSK","sodertalje":"SSK",
    "vik_vasteras_hk":"VIK","vasteras":"VIK",
    "kalmar_hc":"KHC","kalmar":"KHC",
    "ostersunds_ik":"OIK","ostersund":"OIK",
    "vimmerby_hockey":"VHC","vimmerby":"VHC",
    "karlskrona_hk":"KRN","karlskrona":"KRN",
    "kristianstad_ik":"KRI","kristianstad":"KRI",
    "huddinge_ik":"HUD","huddinge":"HUD",
    "vastervik_ik":"VVK","vastervik":"VVK",
    "tingsryd_aif":"TIN","tingsryd":"TIN",
    "demo":"DEMO",
}

# ── 5x7 font (bit4=leftmost) ──────────────────────────────────────────────────
_F: dict[str, list[int]] = {
    "0":[0b01110,0b10001,0b10011,0b10101,0b11001,0b10001,0b01110],
    "1":[0b00100,0b01100,0b00100,0b00100,0b00100,0b00100,0b01110],
    "2":[0b01110,0b10001,0b00001,0b00010,0b00100,0b01000,0b11111],
    "3":[0b11111,0b00010,0b00100,0b00010,0b00001,0b10001,0b01110],
    "4":[0b00010,0b00110,0b01010,0b10010,0b11111,0b00010,0b00010],
    "5":[0b11111,0b10000,0b11110,0b00001,0b00001,0b10001,0b01110],
    "6":[0b00110,0b01000,0b10000,0b11110,0b10001,0b10001,0b01110],
    "7":[0b11111,0b00001,0b00010,0b00100,0b01000,0b01000,0b01000],
    "8":[0b01110,0b10001,0b10001,0b01110,0b10001,0b10001,0b01110],
    "9":[0b01110,0b10001,0b10001,0b01111,0b00001,0b00010,0b01100],
    "-":[0b00000,0b00000,0b00000,0b11111,0b00000,0b00000,0b00000],
    ":":[0b00000,0b00100,0b00000,0b00000,0b00000,0b00100,0b00000],
    " ":[0]*7,
    "A":[0b01110,0b10001,0b10001,0b11111,0b10001,0b10001,0b10001],
    "B":[0b11110,0b10001,0b10001,0b11110,0b10001,0b10001,0b11110],
    "C":[0b01110,0b10001,0b10000,0b10000,0b10000,0b10001,0b01110],
    "D":[0b11110,0b10001,0b10001,0b10001,0b10001,0b10001,0b11110],
    "E":[0b11111,0b10000,0b10000,0b11110,0b10000,0b10000,0b11111],
    "F":[0b11111,0b10000,0b10000,0b11110,0b10000,0b10000,0b10000],
    "G":[0b01110,0b10001,0b10000,0b10111,0b10001,0b10001,0b01110],
    "H":[0b10001,0b10001,0b10001,0b11111,0b10001,0b10001,0b10001],
    "I":[0b01110,0b00100,0b00100,0b00100,0b00100,0b00100,0b01110],
    "J":[0b00111,0b00010,0b00010,0b00010,0b00010,0b10010,0b01100],
    "K":[0b10001,0b10010,0b10100,0b11000,0b10100,0b10010,0b10001],
    "L":[0b10000,0b10000,0b10000,0b10000,0b10000,0b10000,0b11111],
    "M":[0b10001,0b11011,0b10101,0b10001,0b10001,0b10001,0b10001],
    "N":[0b10001,0b11001,0b10101,0b10011,0b10001,0b10001,0b10001],
    "O":[0b01110,0b10001,0b10001,0b10001,0b10001,0b10001,0b01110],
    "P":[0b11110,0b10001,0b10001,0b11110,0b10000,0b10000,0b10000],
    "Q":[0b01110,0b10001,0b10001,0b10001,0b10101,0b10010,0b01101],
    "R":[0b11110,0b10001,0b10001,0b11110,0b10100,0b10010,0b10001],
    "S":[0b01110,0b10001,0b10000,0b01110,0b00001,0b10001,0b01110],
    "T":[0b11111,0b00100,0b00100,0b00100,0b00100,0b00100,0b00100],
    "U":[0b10001,0b10001,0b10001,0b10001,0b10001,0b10001,0b01110],
    "V":[0b10001,0b10001,0b10001,0b10001,0b10001,0b01010,0b00100],
    "W":[0b10001,0b10001,0b10001,0b10101,0b10101,0b11011,0b10001],
    "X":[0b10001,0b10001,0b01010,0b00100,0b01010,0b10001,0b10001],
    "Y":[0b10001,0b10001,0b01010,0b00100,0b00100,0b00100,0b00100],
    "Z":[0b11111,0b00001,0b00010,0b00100,0b01000,0b10000,0b11111],
    "/":[0b00001,0b00010,0b00100,0b01000,0b10000,0b00000,0b00000],
    ".":[0b00000,0b00000,0b00000,0b00000,0b00000,0b00110,0b00110],
}

_WHITE  = (255,255,255)
_YELLOW = (255,220,  0)
_GOLD   = (255,215,  0)
_BLUE   = ( 30,100,220)
_GREEN  = (  0,200,  0)
_RED    = (200,  0,  0)
_GREY   = ( 64, 64, 64)

_TR = str.maketrans("åäöÅÄÖéüÜ","aaoAAOeuu")

def _slug(n:str)->str:
    s=n.translate(_TR).lower()
    s=unicodedata.normalize("NFKD",s).encode("ascii","ignore").decode()
    return re.sub(r"[^a-z0-9]+","_",s).strip("_")

def _h2rgb(h:str)->tuple:
    h=h.lstrip("#"); return int(h[:2],16),int(h[2:4],16),int(h[4:],16)

def _colors(slug:str):
    c=_TC.get(slug)
    if not c:
        for k,v in _TC.items():
            if slug.startswith(k) or k.startswith(slug): c=v; break
    if c:
        p,s,a=c; return _h2rgb(p),_h2rgb(s),(_h2rgb(a) if a else None)
    p,s,a=_FALLBACK; return _h2rgb(p),_h2rgb(s),_h2rgb(a)

def _abbr(slug:str)->str:
    v=_ABBR.get(slug)
    if v: return v
    for k,w in _ABBR.items():
        if slug.startswith(k) or k.startswith(slug): return w
    letters=re.sub(r"[^a-z]","",slug)
    return letters[:4].upper() or "???"

def _strip(px,y0:int,h:int,W:int,p,s,a)->None:
    for row in range(h):
        y=y0+row
        for x in range(W):
            if a is None:
                col=p if x<=(W-1-row*W//h) else s
            else:
                t=x/(W-1); b1=1.0-(row/h)*0.6; b2=b1-0.35
                col=a if t>=b1 else (s if t>=b2 else p)
            px[x,y]=col

def _tw(t:str)->int: return max(0,len(t)*6-1)

def _ch(px,c:str,x0:int,y0:int,col,W:int,H:int)->None:
    rows=_F.get(c.upper(),_F.get("?",[0b11111]*7))
    for dy,bits in enumerate(rows):
        for dx in range(5):
            if bits&(0b10000>>dx):
                nx,ny=x0+dx,y0+dy
                if 0<=nx<W and 0<=ny<H: px[nx,ny]=col

def _txt(px,t:str,x0:int,y0:int,col,W:int,H:int)->None:
    x=x0
    for c in t: _ch(px,c,x,y0,col,W,H); x+=6

def _txtr(px,t:str,xr:int,y0:int,col,W:int,H:int)->None:
    _txt(px,t,xr-_tw(t),y0,col,W,H)

def _txtc(px,t:str,y0:int,col,W:int,H:int)->None:
    _txt(px,t,max(0,(W-_tw(t))//2),y0,col,W,H)

def _dot(px,cx:int,cy:int,col,W:int,H:int,sz:int=3)->None:
    h=sz//2
    for dy in range(-h,h+1):
        for dx in range(-h,h+1):
            nx,ny=cx+dx,cy+dy
            if 0<=nx<W and 0<=ny<H: px[nx,ny]=col

def _dot_colors(plabel:str,live:bool,done:bool,won,ot:bool,so:bool,pscores)->list:
    dots=[_GREY]*5
    if not live and not done: return dots
    def rc(w): return _GREEN if w is True else (_RED if w is False else _YELLOW)
    if done:
        c=rc(won); dots[0]=dots[1]=dots[2]=c
        dots[3]=c if ot else _GREY; dots[4]=c if so else _GREY
        return dots
    idx={"P1":0,"P2":1,"P3":2,"OT":3,"SO":4}.get(plabel or "",0)
    for i in range(5):
        if i<idx:
            if pscores and i<len(pscores):
                ph,pa=pscores[i]; dots[i]=_GREEN if ph>pa else (_RED if ph<pa else _YELLOW)
            else: dots[i]=_GREY
        elif i==idx: dots[i]=_BLUE
        else: dots[i]=_GREY
    return dots


def render(data:dict, team_name:str, now_utc:Optional[datetime]=None)->bytes:
    """Return 32x32 PNG bytes from /team/{team}/now response."""
    if now_utc is None: now_utc=datetime.now(timezone.utc)
    W,H=32,32
    img=Image.new("RGB",(W,H),(0,0,0)); px=img.load()

    cur=data.get("current") or {}
    prev=data.get("previous") or {}
    nxt=data.get("next") or {}

    live=bool(cur.get("is_live")); done=bool(cur.get("is_completed"))

    if cur:
        ht=cur.get("home_team") or team_name; at=cur.get("away_team") or "???"
        hs=int(cur.get("home_score") or 0); as_=int(cur.get("away_score") or 0)
        plabel=cur.get("period_label") or ""; clock=cur.get("period_clock") or ""
        ot=bool(cur.get("is_overtime")); so=bool(cur.get("is_shootout"))
        won=cur.get("won"); goals=cur.get("goals") or []; lg=cur.get("last_goal") or {}
    elif prev:
        ht=prev.get("home_team") or team_name; at=prev.get("away_team") or "???"
        hs=int(prev.get("home_score") or 0); as_=int(prev.get("away_score") or 0)
        plabel=""; clock=""; ot=bool(prev.get("overtime")); so=bool(prev.get("shootout"))
        won=prev.get("won"); goals=[]; lg={}
    else:
        ht=team_name; at="???"; hs=as_=0; plabel=clock=""; ot=so=False; won=None; goals=[]; lg={}

    hslug=_slug(ht); aslug=_slug(at)
    hp,hs_c,ha=_colors(hslug); ap,as_c,aa=_colors(aslug)
    habbr=_abbr(hslug); aabbr=_abbr(aslug)

    # Goal flash: yellow score if last goal < 60s ago
    score_col=_WHITE
    if lg:
        try:
            ts=lg.get("timestamp") or lg.get("time") or ""
            if ts:
                gdt=datetime.fromisoformat(ts)
                if gdt.tzinfo is None: gdt=gdt.replace(tzinfo=timezone.utc)
                if 0<=(now_utc-gdt).total_seconds()<=60: score_col=_YELLOW
        except Exception: pass

    # ── Zone 1 rows 0-7: Home team name in alternating team colors ────────
    home_palette=[hp,hs_c]+([ha] if ha else [])
    x=0
    for i,c in enumerate(habbr):
        col=home_palette[i%len(home_palette)]
        _ch(px,c,x,0,col,W,H); x+=6
    _txtr(px,str(hs),W,0,score_col,W,H)

    # ── Zone 2 rows 8-15: Away team name in alternating team colors ───────
    away_palette=[ap,as_c]+([aa] if aa else [])
    x=0
    for i,c in enumerate(aabbr):
        col=away_palette[i%len(away_palette)]
        _ch(px,c,x,8,col,W,H); x+=6
    _txtr(px,str(as_),W,8,score_col,W,H)

    # ── Zone 3 rows 16-23: Clock / info ──────────────────────────────────
    if live and clock:
        # Show "P2 14:32" style
        info=f"{plabel} {clock}".strip() if plabel else clock
        _txtc(px,info,17,_WHITE,W,H)
    elif live and plabel:
        _txtc(px,plabel,17,_WHITE,W,H)
    elif done and (ot or so):
        suffix="SO" if so else "OT"
        _txtc(px,suffix,17,_GOLD,W,H)
    elif cur and not live and not done:
        # Upcoming today – show time
        dt_str=cur.get("datetime") or ""
        if dt_str:
            try:
                dt=datetime.fromisoformat(dt_str)
                _txtc(px,dt.strftime("%H:%M"),17,_GREY,W,H)
            except Exception:
                pass
    elif nxt:
        dt_str=nxt.get("datetime") or ""
        if dt_str:
            try:
                dt=datetime.fromisoformat(dt_str)
                _txtc(px,dt.strftime("%d/%m"),17,_GREY,W,H)
            except Exception:
                pass

    # ── Zone 4 rows 24-31: Period dots ───────────────────────────────────
    # 5 dots at x positions: 3, 9, 15, 21, 27  (cy=27)
    dot_cols=_dot_colors(plabel,live,done,won,ot,so,None)
    for i,dc in enumerate(dot_cols):
        _dot(px,3+i*6,27,dc,W,H)

    buf=io.BytesIO(); img.save(buf,"PNG"); return buf.getvalue()
