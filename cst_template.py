#!/usr/bin/env python3
"""
cst_template.py - generate a Custom Song Toolkit "Rocksmith DLC template"
(*.dlc.xml) that `packer.exe --build` turns into a finished .psarc using your
installed Wwise 2013.

This writes DataContractSerializer-compatible XML for RocksmithToolkitLib's
DLCPackageData. IMPORTANT correctness rules baked in here, taken from CST source
(github.com/rscustom/rocksmith-custom-song-toolkit):
  * DataContractSerializer with no [DataMember] order attributes emits members
    ALPHABETICALLY. Every complex type below is written in alphabetical order.
  * Root namespace:
    http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.DLCPackage
    Nested types live in their own .NET-namespace-derived namespaces; we use the
    i:type / xmlns mechanics the serializer produces.
  * Dictionary<string,float> KnobValues serialize as the generic
    KeyValueOfstringfloat entries in the System.Collections.Generic namespace.

This is the part that cannot be tested without your CST + Wwise, so treat the
first build as a calibration run and feed any packer.exe error back in.
"""
import html
import os
import uuid

DC = "http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.DLCPackage"
NS_TONE = "http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.DLCPackage.Manifest2014.Tone"
NS_MANIFEST = "http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.DLCPackage.Manifest2014"
NS_ARR = "http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.DLCPackage"
NS_XML = "http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.XML"
NS_AGG = "http://schemas.datacontract.org/2004/07/RocksmithToolkitLib.DLCPackage.AggregateGraph"
NS_GEN = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)


def _knobs(kv):
    """Dictionary<string,float> -> KeyValueOfstringfloat entries (alpha by nothing;
    insertion order is fine for dictionaries)."""
    out = []
    for k, v in kv.items():
        out.append(
            f'<d:KeyValueOfstringfloat>'
            f'<d:Key>{esc(k)}</d:Key>'
            f'<d:Value>{float(v):g}</d:Value>'
            f'</d:KeyValueOfstringfloat>')
    return "".join(out)


def _pedal(tag, pedal):
    """Pedal2014, alphabetical: Category, KnobValues, PedalKey(<Key>), Skin, SkinIndex, Type.
    Note PedalKey serializes as <Key> via [JsonProperty] is JSON-only; DataContract
    uses the property name 'PedalKey'. CST's DataContract uses PedalKey."""
    if pedal is None:
        return f'<{tag} i:nil="true"/>'
    cat = pedal.get("Category")
    kv = pedal.get("KnobValues", {})
    parts = [f"<{tag}>"]
    parts.append(f"<Category>{esc(cat)}</Category>" if cat is not None
                 else '<Category i:nil="true"/>')
    if kv:
        parts.append("<KnobValues xmlns:d=\"%s\">%s</KnobValues>" % (NS_GEN, _knobs(kv)))
    else:
        parts.append('<KnobValues xmlns:d="%s"/>' % NS_GEN)
    parts.append(f"<PedalKey>{esc(pedal.get('Key'))}</PedalKey>")
    skin = pedal.get("Skin")
    parts.append(f"<Skin>{esc(skin)}</Skin>" if skin is not None else '<Skin i:nil="true"/>')
    si = pedal.get("SkinIndex")
    parts.append(f"<SkinIndex>{si:g}</SkinIndex>" if si is not None else '<SkinIndex i:nil="true"/>')
    parts.append(f"<Type>{esc(pedal.get('Type'))}</Type>")
    parts.append(f"</{tag}>")
    return "".join(parts)


def _gear(gl):
    """Gear2014, alphabetical:
    Amp, Cabinet, PostPedal1..4, PrePedal1..4, Rack1..4."""
    order = (["Amp", "Cabinet"]
             + [f"PostPedal{i}" for i in range(1, 5)]
             + [f"PrePedal{i}" for i in range(1, 5)]
             + [f"Rack{i}" for i in range(1, 5)])
    return "<GearList>" + "".join(_pedal(name, gl.get(name)) for name in order) + "</GearList>"


def tone2014_xml(tone, tag="Tone2014"):
    """Tone2014, alphabetical:
    GearList, IsCustom, Key, Name, NameSeparator, SortOrder, ToneDescriptors, Volume."""
    gl = tone["GearList"]
    descs = tone.get("ToneDescriptors", [])
    desc_xml = ("<ToneDescriptors xmlns:e=\"%s\">%s</ToneDescriptors>" % (
        NS_GEN, "".join(f"<e:string>{esc(d)}</e:string>" for d in descs))
        if descs else '<ToneDescriptors xmlns:e="%s"/>' % NS_GEN)
    return (
        f"<{tag} xmlns=\"{NS_TONE}\">"
        f"{_gear(gl)}"
        f"<IsCustom>{str(tone.get('IsCustom', True)).lower()}</IsCustom>"
        f"<Key>{esc(tone.get('Key'))}</Key>"
        f"<Name>{esc(tone.get('Name'))}</Name>"
        f"<NameSeparator>{esc(tone.get('NameSeparator', ' - '))}</NameSeparator>"
        f"<SortOrder>{int(tone.get('SortOrder', 0))}</SortOrder>"
        f"{desc_xml}"
        f"<Volume>{float(str(tone.get('Volume', '-18')).strip()):g}</Volume>"
        f"</{tag}>")


def _arrangement_xml(arr):
    """Arrangement template subset. Build needs the song XML path + descriptive
    fields; SongXml/Sng2014/SongFile are reconstructed by CST from the path.
    Alphabetical order of the members we emit."""
    t = arr["tuning"]  # list of 6 ints (string0..5 offsets-as-absolute? CST uses absolute? -> we pass relative semitone values as CST TuningStrings)
    p = arr
    # alphabetical: ArrangementName, ArrangementPropeties(skip->nil), ArrangementSort,
    # ArrangementType, BonusArr, CapoFret, GlyphsXmlPath, Id, LyricsArtPath, MasterId,
    # Metronome, PluckedType, Represent, RouteMask, ScrollSpeed, Sng2014(nil),
    # SongFile, SongXml, ToneA..D, ToneBase, ToneMultiplayer, Tuning, TuningPitch, TuningStrings
    parts = ["<Arrangement>"]
    parts.append(f"<ArrangementName>{esc(p['arr_name'])}</ArrangementName>")
    # CST reads arrangement.ArrangementPropeties.Represent (note its typo) and
    # NULL-crashes if this is nil. Provide a populated block. Type is
    # SongArrangementProperties2014 in the RocksmithToolkitLib.XML namespace;
    # DataContract orders members alphabetically.
    is_bass = p["arr_type"] == "Bass"
    ap_fields = {
        "BarreChords": 0, "BassPick": 0, "Bends": 1, "BonusArr": 0, "DoubleStops": 0,
        "DropDPower": 0, "FifthsAndOctaves": 0, "FingerPicking": 0, "FretHandMutes": 0,
        "Harmonics": 0, "Hopo": 1, "Metronome": 0, "NonStandardChords": 0, "OpenChords": 1,
        "PalmMutes": 1, "PathBass": 1 if is_bass else 0, "PathLead": 0 if is_bass else 1,
        "PathRhythm": 0, "PickDirection": 0, "PinchHarmonics": 0, "PowerChords": 0,
        "Represent": 1, "RouteMask": {"Lead": 1, "Rhythm": 2, "Bass": 4}.get(p["arr_name"], 1),
        "SlapPop": 0, "Slides": 1, "StandardTuning": 1 if all(t == 0 for t in p["tuning"]) else 0,
        "Sustain": 1, "Syncopation": 0, "Tapping": 0, "Tremolo": 0, "TwoFingerPicking": 0,
        "UnpitchedSlides": 0, "Vibrato": 0,
    }
    ap_xml = "".join(f"<f:{k}>{v}</f:{k}>" for k, v in sorted(ap_fields.items()))
    parts.append(f'<ArrangementPropeties xmlns:f="{NS_XML}">{ap_xml}</ArrangementPropeties>')
    parts.append(f"<ArrangementSort>{p.get('sort', 0)}</ArrangementSort>")
    parts.append(f"<ArrangementType>{esc(p['arr_type'])}</ArrangementType>")
    parts.append(f"<BonusArr>{str(p.get('bonus', False)).lower()}</BonusArr>")
    parts.append(f"<CapoFret>{p.get('capo', 0)}</CapoFret>")
    parts.append('<GlyphsXmlPath i:nil="true"/>')
    parts.append(f"<Id>{p.get('id', uuid.uuid4())}</Id>")
    parts.append('<LyricsArtPath i:nil="true"/>')
    parts.append(f"<MasterId>{p['master_id']}</MasterId>")
    parts.append('<Metronome>None</Metronome>')
    parts.append('<PluckedType>NotPicked</PluckedType>')
    parts.append(f"<Represent>{str(p.get('represent', True)).lower()}</Represent>")
    parts.append(f"<RouteMask>{esc(p['route_mask'])}</RouteMask>")
    parts.append(f"<ScrollSpeed>{p.get('scroll', 13)}</ScrollSpeed>")
    parts.append('<Sng2014 i:nil="true"/>')
    # CST does Path.Combine(templateFolder, File) -> store BARE FILENAME only.
    # SongFile/SongXML live in the AggregateGraph namespace, so the <File> child
    # MUST be qualified with that namespace or it deserializes to null (path2 bug).
    xml_name = os.path.basename(p["xml_path"])
    parts.append(f'<SongFile xmlns:g="{NS_AGG}"><g:File>{esc(xml_name)}</g:File></SongFile>')
    parts.append(f'<SongXml xmlns:g="{NS_AGG}"><g:File>{esc(xml_name)}</g:File></SongXml>')
    for slot in ("A", "B", "C", "D"):
        parts.append(f'<Tone{slot} i:nil="true"/>')
    parts.append(f"<ToneBase>{esc(p['tone_key'])}</ToneBase>")
    parts.append('<ToneMultiplayer i:nil="true"/>')
    parts.append(f"<Tuning>{esc(p.get('tuning_name', 'E Standard'))}</Tuning>")
    parts.append(f"<TuningPitch>{p.get('tuning_pitch', 440.0):g}</TuningPitch>")
    parts.append(
        f'<TuningStrings xmlns:f="{NS_XML}">'
        + "".join(f'<f:String{i}>{t[i]}</f:String{i}>' for i in range(6))
        + "</TuningStrings>")
    parts.append("</Arrangement>")
    return "".join(parts)


def _vocals_arrangement_xml(vocals_xml_name, master_id, vid=None):
    """Vocals arrangement: ArrangementType=Vocal, no tuning/tones, RouteMask=None."""
    import uuid as _u
    parts = ["<Arrangement>"]
    parts.append("<ArrangementName>Vocals</ArrangementName>")
    parts.append('<ArrangementPropeties i:nil="true"/>')
    parts.append("<ArrangementSort>0</ArrangementSort>")
    parts.append("<ArrangementType>Vocal</ArrangementType>")
    parts.append("<BonusArr>false</BonusArr>")
    parts.append("<CapoFret>0</CapoFret>")
    parts.append('<GlyphsXmlPath i:nil="true"/>')
    parts.append(f"<Id>{vid or _u.uuid4()}</Id>")
    parts.append('<LyricsArtPath i:nil="true"/>')
    parts.append(f"<MasterId>{master_id}</MasterId>")
    parts.append('<Metronome>None</Metronome>')
    parts.append('<PluckedType>NotPicked</PluckedType>')
    parts.append("<Represent>true</Represent>")
    parts.append("<RouteMask>None</RouteMask>")
    parts.append("<ScrollSpeed>13</ScrollSpeed>")
    parts.append('<Sng2014 i:nil="true"/>')
    name = os.path.basename(vocals_xml_name)
    parts.append(f'<SongFile xmlns:g="{NS_AGG}"><g:File>{esc(name)}</g:File></SongFile>')
    parts.append(f'<SongXml xmlns:g="{NS_AGG}"><g:File>{esc(name)}</g:File></SongXml>')
    for slot in ("A", "B", "C", "D"):
        parts.append(f'<Tone{slot} i:nil="true"/>')
    parts.append('<ToneBase i:nil="true"/>')
    parts.append('<ToneMultiplayer i:nil="true"/>')
    parts.append('<Tuning i:nil="true"/>')
    parts.append("<TuningPitch>440</TuningPitch>")
    parts.append(
        f'<TuningStrings xmlns:f="{NS_XML}">'
        + "".join(f'<f:String{i}>0</f:String{i}>' for i in range(6))
        + "</TuningStrings>")
    parts.append("</Arrangement>")
    return "".join(parts)


def build_dlc_xml(meta, arrangements, tones, out_path):
    """meta: dict(dlc_key, title, artist, album, year, avg_tempo, art_path,
    audio_path, audio_preview_path, volume, preview_volume).
    arrangements: list of arr dicts. tones: list of Tone2014 preset dicts.
    Writes the .dlc.xml and returns its path. All file references are stored as
    BARE FILENAMES because CST does Path.Combine(templateFolder, name)."""
    meta = dict(meta)
    for k in ("art_path", "audio_path", "audio_preview_path"):
        if meta.get(k):
            meta[k] = os.path.basename(meta[k])
    arr_xml = "".join(_arrangement_xml(a) for a in arrangements)
    if meta.get("vocals_xml"):
        import random as _r
        arr_xml += _vocals_arrangement_xml(meta["vocals_xml"], _r.randint(1, 10**8))
    tones_xml = "".join(tone2014_xml(t) for t in tones)

    # DLCPackageData alphabetical top-level members (subset we set; rest nil/default)
    body = []
    body.append(f"<AlbumArtPath>{esc(meta.get('art_path', ''))}</AlbumArtPath>")
    body.append(f'<AppId>248750</AppId>')
    body.append(f"<Arrangements>{arr_xml}</Arrangements>")
    body.append('<DefaultShowlights>false</DefaultShowlights>')
    body.append('<GameVersion>RS2014</GameVersion>')
    body.append('<Mac>false</Mac>')
    body.append(f"<Name>{esc(meta['dlc_key'])}</Name>")
    body.append(f"<OggPath>{esc(meta.get('audio_path', ''))}</OggPath>")
    body.append(f"<OggPreviewPath>{esc(meta.get('audio_preview_path', ''))}</OggPreviewPath>")
    body.append('<OggQuality>4</OggQuality>')
    body.append('<PS3>false</PS3>')
    body.append('<Pc>true</Pc>')
    body.append(f"<PreviewVolume>{meta.get('preview_volume', -5.0):g}</PreviewVolume>")
    body.append('<SignatureType>CON</SignatureType>')
    # SongInfo alphabetical: Album, AlbumSort, Artist, ArtistSort, AverageTempo,
    # JapaneseArtistName, JapaneseSongName, SongDisplayName, SongDisplayNameSort, SongYear
    si = (
        "<SongInfo>"
        f"<Album>{esc(meta.get('album', ''))}</Album>"
        f"<AlbumSort>{esc(meta.get('album', ''))}</AlbumSort>"
        f"<Artist>{esc(meta['artist'])}</Artist>"
        f"<ArtistSort>{esc(meta['artist'])}</ArtistSort>"
        f"<AverageTempo>{int(meta.get('avg_tempo', 120))}</AverageTempo>"
        '<JapaneseArtistName i:nil="true"/>'
        '<JapaneseSongName i:nil="true"/>'
        f"<SongDisplayName>{esc(meta['title'])}</SongDisplayName>"
        f"<SongDisplayNameSort>{esc(meta['title'])}</SongDisplayNameSort>"
        f"<SongYear>{int(meta.get('year', 2026))}</SongYear>"
        "</SongInfo>")
    body.append(si)
    body.append(f"<Tones xmlns:t=\"{NS_MANIFEST}.Tone\"/>")  # RS2012 list empty
    body.append(f"<TonesRS2014>{tones_xml}</TonesRS2014>")
    body.append(
        "<ToolkitInfo>"
        f"<PackageAuthor>{esc(meta.get('author', 'GP2Rocksmith'))}</PackageAuthor>"
        "<PackageComment>Made with GP2Rocksmith Studio</PackageComment>"
        f"<PackageRating>0</PackageRating>"
        f"<PackageVersion>{esc(meta.get('pkg_version', '1'))}</PackageVersion>"
        "<ToolkitVersion>2.9.2.1</ToolkitVersion>"
        "</ToolkitInfo>")
    body.append(f"<Volume>{meta.get('volume', -7.0):g}</Volume>")
    body.append('<XBox360>false</XBox360>')

    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<DLCPackageData xmlns="{DC}" '
        f'xmlns:i="{XSI}">'
        + "".join(body) +
        "</DLCPackageData>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return out_path
