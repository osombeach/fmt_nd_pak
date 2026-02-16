#!/usr/bin/env python3
"""Lightweight Naughty Dog .pak animation resource probe.

This utility does not depend on Noesis. It parses pak headers/login tables and
prints discovered ANIM_GROUP resources (with heuristic metadata/string probing).
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

MAGICS = {2681, 68217, 2147486329, 2685, 68221}
TP1_MAGICS = {2685, 68221}

TP1_PAK_STRING_IDS = {
    0x50CAF5257D6A140B: "JOINT_HIERARCHY",
    0x349D779A792F45C1: "GEOMETRY_1",
    0xCE3ADE693131B309: "VRAM_DESC",
    0xE7254422A7A8F476: "VRAM_DESC_TABLE",
    0xA2481DA1A5D2CE2B: "TEXTURE_TABLE",
    0x36125D3CFB7F3991: "TEXTURE_DICTIONARY",
    0x4903731234F1BEA6: "PAK_LOGIN_TABLE",
    0x61DE7E6141BC6F2B: "EFFECT_TABLE",
    0x460F497540A29F73: "SPAWNER_GROUP",
    0x596A72779C4C87D: "TAG_INT",
    0x53DE1E1977F9CBA4: "ANIM_GROUP",
    0x791137002DB17EBB: "MATERIAL_TABLE_1",
    0x384ADF724B123839: "FOREGROUND_SECTION_2",
    0x5ADB4A2D2E2A6EB: "COLLISION_DATA_CLOTH",
    0x3A3BB43D817C93DE: "TAG_VEC4",
    0x35EB8812D3A2D576: "TAG_FLOAT",
    0x6A98005088A56C5: "LEVEL_BOUNDING_BOX_DATA",
    0x438E1B0DBFFAA93: "AMBSHADOWS_OCCLUDER_INFO",
    0x7D9BFD5CEC879080: "COLLISION_DATA_FOREGROUND",
    0xEC3AFEDF7EF282F0: "SOUND_BANK_TABLE",
}


@dataclass
class PakHeader:
    magic: int
    pak_login_page_idx: int
    pak_login_item_off: int
    page_count: int
    page_entry_table: int
    pointer_fixup_table_off: int
    is_tp1: bool


@dataclass
class PageEntry:
    start: int
    size_a: int
    size_b: int


@dataclass
class LoginEntry:
    page: int
    offset: int
    item_type: str


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def u64(buf: bytes, off: int) -> int:
    return struct.unpack_from("<Q", buf, off)[0]


def i64(buf: bytes, off: int) -> int:
    return struct.unpack_from("<q", buf, off)[0]


def cstring(buf: bytes, off: int) -> str:
    if off < 0 or off >= len(buf):
        return ""
    end = buf.find(b"\x00", off)
    if end == -1:
        end = len(buf)
    data = buf[off:end]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def is_probably_text(s: str) -> bool:
    return len(s) >= 3 and all((32 <= ord(ch) < 127) for ch in s[:128])


def parse_header(buf: bytes) -> PakHeader:
    magic = u32(buf, 0)
    if magic not in MAGICS:
        raise ValueError(f"Not a known Naughty Dog pak magic: 0x{magic:08X}")
    return PakHeader(
        magic=magic,
        pak_login_page_idx=u32(buf, 8),
        pak_login_item_off=u32(buf, 12),
        page_count=u32(buf, 16),
        page_entry_table=u32(buf, 20),
        pointer_fixup_table_off=u32(buf, 28),
        is_tp1=magic in TP1_MAGICS,
    )


def parse_pages(buf: bytes, hdr: PakHeader) -> List[PageEntry]:
    out: List[PageEntry] = []
    pos = hdr.page_entry_table
    for _ in range(hdr.page_count):
        out.append(PageEntry(u32(buf, pos), u32(buf, pos + 4), u32(buf, pos + 8)))
        pos += 12
    return out


def parse_pointer_fixups(buf: bytes, pages: List[PageEntry], hdr: PakHeader) -> Dict[int, int]:
    pft = hdr.pointer_fixup_table_off
    data_off = u32(buf, pft + 4)
    count = u32(buf, pft + 8)
    pos = data_off
    out: Dict[int, int] = {}
    for _ in range(count):
        page1 = struct.unpack_from("<H", buf, pos)[0]
        page2 = struct.unpack_from("<H", buf, pos + 2)[0]
        pointer_off = u32(buf, pos + 4)
        read_addr = pointer_off + pages[page1].start
        out[read_addr] = page2
        pos += 8
    return out


def read_pointer_fixup(buf: bytes, pages: List[PageEntry], ptr_map: Dict[int, int], read_addr: int, tp1_zero: bool = False) -> int:
    val = i64(buf, read_addr)
    if val > 0 or tp1_zero:
        page = ptr_map.get(read_addr)
        if page is not None:
            return val + pages[page].start
    return val


def type_map_non_tp1(buf: bytes, pages: List[PageEntry]) -> Dict[Tuple[int, int], str]:
    out: Dict[Tuple[int, int], str] = {}
    for p, page in enumerate(pages):
        num_hdr = struct.unpack_from("<H", buf, page.start + 18)[0]
        pos = page.start + 20
        for _ in range(num_hdr):
            _name_ptr = u64(buf, pos)
            res_off = u32(buf, pos + 8)
            item_start = page.start + res_off
            type_str_off = u64(buf, item_start + 8) + page.start
            item_type = cstring(buf, type_str_off)
            out[(p, res_off)] = item_type
            pos += 16
    return out


def parse_login_entries(buf: bytes, hdr: PakHeader, pages: List[PageEntry], ptr_map: Dict[int, int]) -> List[LoginEntry]:
    login_item_start = pages[hdr.pak_login_page_idx].start + hdr.pak_login_item_off
    marker = u32(buf, login_item_start + 32)
    res_padding = 48 if (hdr.is_tp1 or marker == 74565) else 32

    login_count = u32(buf, login_item_start + res_padding)
    pos = login_item_start + res_padding + 8

    non_tp1_types = {} if hdr.is_tp1 else type_map_non_tp1(buf, pages)

    entries: List[LoginEntry] = []
    for _ in range(login_count):
        page = u32(buf, pos)
        offset = u32(buf, pos + 4)
        item_type = "UNKNOWN"
        item_start = pages[page].start + offset

        if hdr.is_tp1:
            type_sid = u64(buf, item_start + 32)
            item_type = TP1_PAK_STRING_IDS.get(type_sid, f"TYPE_ID_0x{type_sid:016X}")
        else:
            item_type = non_tp1_types.get((page, offset), "UNKNOWN")

        entries.append(LoginEntry(page=page, offset=offset, item_type=item_type))
        pos += 8
    return entries


def probe_anim_group(buf: bytes, pages: List[PageEntry], ptr_map: Dict[int, int], entry: LoginEntry) -> dict:
    item_start = pages[entry.page].start + entry.offset
    base = item_start + 32

    header_u32 = [u32(buf, base + i * 4) for i in range(16)]
    header_u64 = [u64(buf, base + i * 8) for i in range(8)]

    guessed_strings: List[str] = []
    for rel in range(0, 0x140, 8):
        read_addr = base + rel
        if read_addr + 8 > len(buf):
            break
        target = read_pointer_fixup(buf, pages, ptr_map, read_addr, tp1_zero=True)
        if 0 <= target < len(buf):
            s = cstring(buf, target)
            if is_probably_text(s):
                guessed_strings.append(s)

    # Fallback raw string scan in local window (helps when not pointer-fixup backed)
    local = buf[base : min(base + 0x300, len(buf))]
    for token in local.split(b"\x00"):
        if 3 <= len(token) <= 80:
            try:
                s = token.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if is_probably_text(s):
                guessed_strings.append(s)

    dedup = []
    seen = set()
    for s in guessed_strings:
        if s not in seen:
            dedup.append(s)
            seen.add(s)

    return {
        "page": entry.page,
        "offset": entry.offset,
        "item_start": item_start,
        "header_u32": header_u32,
        "header_u64": header_u64,
        "guessed_strings": dedup[:50],
    }


def resolve_input_path(path: Path, search_roots: List[Path]) -> Path:
    if path.exists():
        return path
    for root in search_roots:
        candidate = root / path
        if candidate.exists():
            return candidate
    # fallback: basename lookup in search roots
    for root in search_roots:
        for hit in root.glob(f"**/{path.name}"):
            if hit.is_file():
                return hit
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe animation resources from Naughty Dog .pak files")
    ap.add_argument("pak", type=Path, nargs="+", help="Path(s) to .pak file(s) (e.g. anim-elena.pak)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    ap.add_argument(
        "--search-root",
        type=Path,
        action="append",
        default=[],
        help="Extra root path(s) to search when pak filename is not found from cwd",
    )
    args = ap.parse_args()

    roots = [Path.cwd(), *args.search_root]
    all_results = []

    for input_path in args.pak:
        path = resolve_input_path(input_path, roots)
        if not path.exists():
            print(
                f"ERROR: pak not found: {input_path} (cwd={Path.cwd()})\n"
                "Tip: place the file in the current working directory or pass --search-root <path>."
            )
            return 2

        buf = path.read_bytes()
        hdr = parse_header(buf)
        pages = parse_pages(buf, hdr)
        ptr_map = parse_pointer_fixups(buf, pages, hdr)
        entries = parse_login_entries(buf, hdr, pages, ptr_map)

        anim_entries = [e for e in entries if e.item_type == "ANIM_GROUP"]
        anim_groups = [probe_anim_group(buf, pages, ptr_map, e) for e in anim_entries]

        result = {
            "file": str(path),
            "magic": hdr.magic,
            "is_tp1": hdr.is_tp1,
            "page_count": hdr.page_count,
            "entry_count": len(entries),
            "anim_group_count": len(anim_entries),
            "entries": [{"page": e.page, "offset": e.offset, "type": e.item_type} for e in entries],
            "anim_groups": anim_groups,
        }
        all_results.append(result)

    if args.json:
        print(json.dumps(all_results if len(all_results) > 1 else all_results[0], indent=2))
    else:
        for result in all_results:
            print(f"File: {result['file']}")
            print(f"Magic: {result['magic']} (TP1={result['is_tp1']})")
            print(f"Pages: {result['page_count']}, Login entries: {result['entry_count']}")
            print(f"ANIM_GROUP entries: {result['anim_group_count']}")
            for i, group in enumerate(result["anim_groups"]):
                print(f"\n[ANIM_GROUP #{i}] page={group['page']} offset=0x{group['offset']:X} start=0x{group['item_start']:X}")
                print("  header_u32:", " ".join(f"0x{x:08X}" for x in group["header_u32"][:8]))
                if group["guessed_strings"]:
                    print("  guessed strings:")
                    for s in group["guessed_strings"][:12]:
                        print("   -", s)
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
