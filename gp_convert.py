"""Convert old Guitar Pro formats (.gp3/.gp4/.gp5/.gpx) to a GP7/8 .gp (gpif zip)
that gp2rs can read, using PyGuitarPro."""
import io, zipfile, html

_NV = {1:"Whole",2:"Half",4:"Quarter",8:"Eighth",16:"16th",32:"32nd",64:"64th",128:"128th"}

def _esc(s): return html.escape(str(s), quote=True)

def convert_song(song, out_path):
    ids = {"bar":0,"voice":0,"beat":0,"note":0,"rhythm":0}
    def nid(k):
        ids[k]+=1; return ids[k]

    tracks_xml=[]; masterbars=[]; bars=[]; voices=[]; beats=[]; notes=[]; rhythms=[]
    nmeasures = max((len(t.measures) for t in song.tracks), default=0)

    # per (track, measure) -> bar id
    bar_grid = {}
    for ti, tr in enumerate(song.tracks):
        for mi in range(nmeasures):
            bar_grid[(ti,mi)] = nid("bar")

    # build bars/voices/beats/notes
    for ti, tr in enumerate(song.tracks):
        for mi in range(nmeasures):
            bid = bar_grid[(ti,mi)]
            meas = tr.measures[mi] if mi < len(tr.measures) else None
            vids=[]
            vlist = meas.voices if meas else []
            any_voice=False
            for vc in vlist:
                if not vc.beats:
                    continue
                any_voice=True
                vid=nid("voice"); vids.append(vid)
                beids=[]
                for be in vc.beats:
                    beid=nid("beat")
                    # rhythm
                    d=be.duration
                    nv=_NV.get(d.value,"Quarter")
                    dots=""
                    if getattr(d,"isDoubleDotted",False): dots='<AugmentationDot count="2"/>'
                    elif getattr(d,"isDotted",False): dots='<AugmentationDot count="1"/>'
                    tup=""
                    tp=getattr(d,"tuplet",None)
                    if tp and getattr(tp,"enters",1)!=getattr(tp,"times",1):
                        tup='<PrimaryTuplet num="%d" den="%d"/>'%(tp.enters,tp.times)
                    rid=nid("rhythm")
                    rhythms.append('<Rhythm id="%d"><NoteValue>%s</NoteValue>%s%s</Rhythm>'%(rid,nv,dots,tup))
                    # notes
                    nids=[]
                    nstrings=len(tr.strings)
                    for nt in (be.notes or []):
                        gpif_string = nstrings - nt.string  # 0 = lowest
                        props=['<Property name="String"><String>%d</String></Property>'%gpif_string,
                               '<Property name="Fret"><Fret>%d</Fret></Property>'%nt.value]
                        eff=getattr(nt,"effect",None)
                        extra=""
                        if eff is not None:
                            if getattr(eff,"palmMute",False): props.append('<Property name="PalmMuted"><Enable/></Property>')
                            if getattr(eff,"vibrato",False): props.append('<Property name="Vibrato"><Enable/></Property>')
                            if getattr(eff,"hammer",False): props.append('<Property name="HopoDestination"><Enable/></Property>')
                            h=getattr(eff,"harmonic",None)
                            if h is not None and getattr(h,"type",0):
                                ht="Natural" if h.__class__.__name__=="NaturalHarmonic" else "Pinch"
                                props.append('<Property name="HarmonicType"><HType>%s</HType></Property>'%ht)
                            sl=getattr(eff,"slides",None)
                            if sl:
                                flags=0
                                for s in sl:
                                    sn=s.name if hasattr(s,"name") else str(s)
                                    if "shiftSlideTo" in sn: flags|=0b01
                                    elif "legatoSlideTo" in sn: flags|=0b10
                                    elif "outDownwards" in sn: flags|=0b0100
                                    elif "outUpwards" in sn: flags|=0b1000
                                if flags: props.append('<Property name="Slide"><Flags>%d</Flags></Property>'%flags)
                            b_=getattr(eff,"bend",None)
                            if b_ is not None and getattr(b_,"points",None):
                                pts=b_.points
                                dest=max(p.value for p in pts)
                                props.append('<Property name="Bended"><Enable/></Property>')
                                props.append('<Property name="BendDestinationValue"><Float>%d</Float></Property>'%dest)
                            if getattr(eff,"accentuatedNote",False) or getattr(eff,"heavyAccentuatedNote",False):
                                extra+='<Accent>8</Accent>'
                        nty=getattr(nt,"type",None)
                        tyname=nty.name if nty is not None and hasattr(nty,"name") else ""
                        if tyname=="dead": props.append('<Property name="Muted"><Enable/></Property>')
                        tie='<Tie destination="true"/>' if tyname=="tie" else ""
                        nidv=nid("note")
                        notes.append('<Note id="%d"><Properties>%s</Properties>%s%s</Note>'%(nidv,"".join(props),tie,extra))
                        nids.append(str(nidv))
                    beats.append('<Beat id="%d"><Rhythm ref="%d"/><Notes>%s</Notes></Beat>'%(beid,rid," ".join(nids)))
                    beids.append(str(beid))
                voices.append('<Voice id="%d"><Beats>%s</Beats></Voice>'%(vid," ".join(beids)))
            if not any_voice:
                vid=nid("voice"); vids.append(vid)
                voices.append('<Voice id="%d"><Beats></Beats></Voice>'%vid)
            bars.append('<Bar id="%d"><Voices>%s</Voices></Bar>'%(bid," ".join(str(v) for v in vids)))

    # masterbars (time sig + section markers from track 0)
    t0 = song.tracks[0]
    for mi in range(nmeasures):
        hdr = t0.measures[mi].header if mi < len(t0.measures) else None
        ts = "4/4"
        sect=""
        if hdr is not None:
            ts="%d/%d"%(hdr.timeSignature.numerator, hdr.timeSignature.denominator.value)
            mk=getattr(hdr,"marker",None)
            if mk and getattr(mk,"title",None): sect='<Section><Text>%s</Text></Section>'%_esc(mk.title)
        ref=" ".join(str(bar_grid[(ti,mi)]) for ti in range(len(song.tracks)))
        masterbars.append('<MasterBar><Time>%s</Time>%s<Bars>%s</Bars></MasterBar>'%(ts,sect,ref))

    # tracks
    for ti, tr in enumerate(song.tracks):
        pitches=" ".join(str(s.value) for s in reversed(tr.strings))
        cap=getattr(tr,"capo",0) or 0
        tracks_xml.append(
            '<Track id="%d"><Name>%s</Name><Properties>'
            '<Property name="Tuning"><Pitches>%s</Pitches></Property>'
            '<Property name="CapoFret"><Fret>%d</Fret></Property>'
            '</Properties></Track>'%(ti,_esc(tr.name or "Track %d"%ti),pitches,cap))

    # tempo
    tempo=int(getattr(song,"tempo",120) or 120)
    gpif=('<?xml version="1.0" encoding="UTF-8"?><GPIF>'
        '<Score><Title>%s</Title><Artist>%s</Artist><Album>%s</Album></Score>'
        '<MasterTrack><Automations><Automation><Type>Tempo</Type><Bar>0</Bar>'
        '<Position>0</Position><Value>%d 2</Value></Automation></Automations></MasterTrack>'
        '<Tracks>%s</Tracks><MasterBars>%s</MasterBars><Bars>%s</Bars>'
        '<Voices>%s</Voices><Beats>%s</Beats><Notes>%s</Notes><Rhythms>%s</Rhythms>'
        '</GPIF>'%(_esc(song.title),_esc(song.artist),_esc(getattr(song,"album","") or ""),
                  tempo,"".join(tracks_xml),"".join(masterbars),"".join(bars),
                  "".join(voices),"".join(beats),"".join(notes),"".join(rhythms)))

    with zipfile.ZipFile(out_path,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("Content/score.gpif", gpif)
    return out_path

def convert(old_path, out_path):
    import guitarpro
    return convert_song(guitarpro.parse(old_path), out_path)
