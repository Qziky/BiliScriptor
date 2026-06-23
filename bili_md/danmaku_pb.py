from __future__ import annotations

from typing import Any


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, offset
        shift += 7
    raise ValueError("Truncated protobuf varint.")


def _skip_field(data: bytes, offset: int, wire_type: int) -> int:
    if wire_type == 0:
        _, offset = _read_varint(data, offset)
        return offset
    if wire_type == 1:
        return offset + 8
    if wire_type == 2:
        length, offset = _read_varint(data, offset)
        return offset + length
    if wire_type == 5:
        return offset + 4
    raise ValueError(f"Unsupported protobuf wire type: {wire_type}")


def _read_length_delimited(data: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = _read_varint(data, offset)
    end = offset + length
    if end > len(data):
        raise ValueError("Truncated protobuf bytes field.")
    return data[offset:end], end


def _decode_elem(blob: bytes) -> dict[str, Any]:
    elem: dict[str, Any] = {}
    offset = 0
    while offset < len(blob):
        key, offset = _read_varint(blob, offset)
        field_no = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            value, offset = _read_varint(blob, offset)
            if field_no == 1:
                elem["id"] = value
            elif field_no == 2:
                elem["progress"] = value
            elif field_no == 3:
                elem["mode"] = value
            elif field_no == 4:
                elem["fontsize"] = value
            elif field_no == 5:
                elem["color"] = value
            elif field_no == 7:
                # Older references sometimes describe field 7 as ctime.
                # Current web seg.so uses field 7 for content when it is length-delimited.
                elem["ctime"] = value
            elif field_no == 8:
                elem["ctime"] = value
            elif field_no == 10:
                elem["field_10"] = value
            elif field_no == 9:
                elem["weight"] = value
            elif field_no == 11:
                elem["pool"] = value
            elif field_no == 13:
                elem["attr"] = value
            else:
                elem[f"field_{field_no}"] = value
        elif wire_type == 2:
            raw, offset = _read_length_delimited(blob, offset)
            try:
                value: str | bytes = raw.decode("utf-8")
            except UnicodeDecodeError:
                value = raw
            if field_no == 6:
                elem["user_hash"] = value
                elem.setdefault("content", value)
            elif field_no == 7:
                elem["content"] = value
            elif field_no == 10:
                elem["action"] = value
            elif field_no in (11, 12):
                elem["id_str"] = value
            elif field_no in (13, 22):
                elem["animation"] = value
            else:
                elem[f"field_{field_no}"] = value
        else:
            offset = _skip_field(blob, offset, wire_type)
    return elem


def decode_dm_seg_mobile_reply(data: bytes) -> list[dict[str, Any]]:
    elems: list[dict[str, Any]] = []
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_no = key >> 3
        wire_type = key & 0x07
        if field_no == 1 and wire_type == 2:
            blob, offset = _read_length_delimited(data, offset)
            elems.append(_decode_elem(blob))
        else:
            offset = _skip_field(data, offset, wire_type)
    return elems
