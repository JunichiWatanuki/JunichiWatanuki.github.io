"""Decode Garmin FIT files and render training articles from their messages.

FIT field scaling, enum conversion and timestamp conversion are performed by
Garmin's official SDK.  This module does not write output files.
"""
from __future__ import annotations

import html
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

JST = timezone(timedelta(hours=9), name="JST")
UTC = timezone.utc
MISSING = "—"
# Values below date_time.min in the FIT profile are relative, not UTC dates.
FIT_ABSOLUTE_MIN = datetime(1989, 12, 31, tzinfo=UTC) + timedelta(seconds=0x10000000)


def _json_compatible(value, location: str = "messages"):
    """Preserve all SDK values, converting them to standard JSON types."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Non-finite number in FIT data at {location}")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return list(bytes(value))
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item, f"{location}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            # The SDK uses integer keys for unknown FIT fields; JSON requires
            # string object keys. Do not silently lose data on a collision.
            if isinstance(key, str):
                json_key = key
            elif isinstance(key, int) and not isinstance(key, bool):
                json_key = str(key)
            else:
                raise ValueError(f"Unsupported FIT object key at {location}: {type(key).__name__}")
            if json_key in result:
                raise ValueError(f"Duplicate JSON key in FIT data at {location}: {json_key!r}")
            result[json_key] = _json_compatible(item, f"{location}.{json_key}")
        return result
    raise ValueError(f"Unsupported FIT value at {location}: {type(value).__name__}")


def _as_jst(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        # A date alone is not a recorded timestamp. fromisoformat otherwise
        # accepts it and silently invents midnight.
        if not any(separator in text for separator in ("T", "t", " ")):
            raise ValueError("timestamp must include both date and time")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("invalid ISO timestamp") from exc
    else:
        raise ValueError("timestamp must be an ISO string or datetime")
    try:
        if dt.utcoffset() is None:
            dt = dt.replace(tzinfo=UTC)
        if dt.astimezone(UTC) < FIT_ABSOLUTE_MIN:
            raise ValueError("FIT timestamp is relative, not an absolute date")
        return dt.astimezone(JST)
    except (ValueError, OverflowError) as exc:
        raise ValueError("timestamp cannot be converted to JST") from exc


def created_at(messages: dict) -> datetime:
    """Return the first FIT file ID's creation timestamp in Japan time.

    A naive timestamp is interpreted as UTC, as are the SDK's FIT times.
    The first file ID identifies the source; do not substitute a later session
    or another file ID if this essential timestamp is missing.
    """
    if not isinstance(messages, dict):
        raise ValueError("FIT messages must be a dictionary")
    file_ids = messages.get("file_id_mesgs")
    if not isinstance(file_ids, (list, tuple)) or not file_ids or not isinstance(file_ids[0], dict):
        raise ValueError("FIT file_id_mesgs is missing or invalid")
    value = file_ids[0].get("time_created")
    if value is None or value == "":
        raise ValueError("FIT file_id_mesgs[0].time_created is missing")
    try:
        return _as_jst(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("FIT file_id_mesgs[0].time_created is invalid or cannot be converted to JST") from exc


def decode_fit(source: Path) -> dict:
    """Validate and decode a FIT file to JSON-compatible SDK messages.

    Reject invalid FIT headers, CRC failures, SDK decoding errors and missing
    creation timestamps instead of returning partially decoded data.
    """
    try:
        from garmin_fit_sdk import Decoder, Stream
    except ImportError as exc:
        raise RuntimeError("garmin-fit-sdk is required to decode FIT files") from exc

    source = Path(source)
    try:
        stream = Stream.from_file(str(source))
    except OSError as exc:
        raise ValueError(f"Cannot open FIT file {source}: {exc}") from exc
    try:
        decoder = Decoder(stream)
        if not decoder.is_fit():
            raise ValueError(f"Not a valid FIT file: {source}")
        if not decoder.check_integrity():
            raise ValueError(f"FIT integrity/CRC check failed: {source}")
        # check_integrity consumes the stream; read must begin at the header.
        stream.reset()
        try:
            messages, errors = decoder.read(enable_crc_check=True)
        except Exception as exc:
            raise ValueError(f"FIT decoding failed for {source}: {exc}") from exc
        if errors:
            details = "; ".join(str(error) or type(error).__name__ for error in errors)
            raise ValueError(f"FIT decoding failed for {source}: {details}")
        if not isinstance(messages, dict):
            raise ValueError(f"FIT decoder returned invalid messages for {source}")
        try:
            normalized = _json_compatible(messages)
            created_at(normalized)
        except ValueError as exc:
            raise ValueError(f"Invalid FIT data in {source}: {exc}") from exc
        return normalized
    finally:
        stream.close()


def _number(value):
    """Return a usable, nonnegative FIT measurement, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if value < 0 or not math.isfinite(value):
            return None
    except OverflowError:
        return None
    return value


def _first_number(record: dict, *keys: str):
    for key in keys:
        value = _number(record.get(key))
        if value is not None:
            return value
    return None


def _plain_number(value, decimals: int = 2) -> str:
    number = _number(value)
    if number is None:
        return MISSING
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{number:.0f}"


def _quantity(value, unit: str, divisor: float = 1, decimals: int = 2) -> str:
    number = _number(value)
    if number is None:
        return MISSING
    # Scale only valid present measurements; in particular zero is not missing.
    text = _plain_number(number / divisor, decimals)
    return f"{text} {unit}" if text != MISSING else MISSING


def _duration(value) -> str:
    number = _number(value)
    if number is None:
        return MISSING
    # Round to the nearest second; preserve hours greater than 24.
    seconds = math.floor(number + 0.5)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _time(value) -> str:
    if value is None or value == "":
        return MISSING
    try:
        return _as_jst(value).strftime("%Y-%m-%d %H:%M:%S JST")
    except (TypeError, ValueError, OverflowError):
        return MISSING


def _text(value) -> str:
    if value is None or value == "" or (isinstance(value, str) and value.lower() in ("unknown", "invalid")):
        return MISSING
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return MISSING
    if isinstance(value, float) and not math.isfinite(value):
        return MISSING
    return str(value)


def _rows(messages: dict, key: str) -> list[dict]:
    rows = messages.get(key, [])
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"FIT {key} must be a list of message objects")
    return list(rows)


def _sport_title(sessions: list[dict]) -> str:
    """Describe each recorded session sport without guessing missing values."""
    labels = []
    for session in sessions:
        sport = session.get("sport")
        if isinstance(sport, str):
            sport = " ".join(sport.split())
            normalized = sport.lower()
            if normalized in ("", "unknown", "invalid", "generic", "all"):
                label = "Activity"
            else:
                label = {"running": "Jog", "swimming": "Swim"}.get(normalized, sport)
        elif isinstance(sport, int) and not isinstance(sport, bool) and sport >= 0:
            # FIT sport 0 is generic, 254 is all, and 255 is the invalid enum.
            # Other numeric values are identifiers, not known sports.
            label = "Activity" if sport in (0, 254, 255) else f"Sport {sport}"
        else:
            label = "Activity"
        if label not in labels:
            labels.append(label)
    return " / ".join(labels) if labels else "Activity"


def _is_pool_lap(lap: dict, sessions: list[dict]) -> bool:
    """Identify pool swimming without guessing among multiple sessions."""
    sport, sub_sport = lap.get("sport"), lap.get("sub_sport")
    if sport not in (None, "", "swimming"):
        return False
    if sub_sport == "lap_swimming":
        return True
    if sub_sport not in (None, ""):
        return False
    # FIT laps sometimes omit sport details that are present on the session.
    # It is safe to inherit only when there is exactly one session.
    return (len(sessions) == 1 and sessions[0].get("sport") == "swimming"
            and sessions[0].get("sub_sport") == "lap_swimming")


def _length_count(value):
    """A recorded count must be a nonnegative integer, not a bool."""
    number = _number(value)
    if number is None or (isinstance(number, float) and not number.is_integer()):
        return None
    return int(number)


def _github_links(source: Path, root: Path, json_relative: Path) -> tuple[str, str]:
    root = Path(root).resolve()
    source = Path(source)
    source_root = root / "data" / "sources" / "training"
    if not source.is_absolute():
        # Accept either a path relative to the repository or an already
        # cwd-relative source path under that repository.
        cwd_candidate = source.resolve()
        try:
            cwd_candidate.relative_to(source_root)
            source = cwd_candidate
        except ValueError:
            source = root / source
    source = source.resolve()
    try:
        source.relative_to(source_root)
        source_relative = source.relative_to(root)
    except ValueError as exc:
        raise ValueError("FIT source must be under data/sources/training") from exc
    if source.suffix.lower() != ".fit":
        raise ValueError("FIT source must have a .fit extension")
    json_relative = Path(json_relative)
    parts = json_relative.parts
    if (json_relative.is_absolute() or len(parts) < 4
            or parts[:3] != ("data", "contents", "training")
            or any(part in (".", "..") for part in parts)
            or json_relative.suffix.lower() != ".json"):
        raise ValueError("JSON path must be a repository-relative data/contents/training/.../*.json path")

    repo = os.environ.get("GITHUB_REPOSITORY") or "JunichiWatanuki/JunichiWatanuki.github.io"
    repo_parts = repo.split("/")
    if len(repo_parts) != 2 or any(not p or p in (".", "..") for p in repo_parts):
        raise ValueError("GITHUB_REPOSITORY must be an owner/repository name")
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    if ref in (".", ".."):
        raise ValueError("GITHUB_REF_NAME must identify a Git ref")
    base = "https://github.com/" + "/".join(quote(p, safe="") for p in repo_parts)
    base += "/blob/" + quote(ref, safe="") + "/"
    return (base + quote(source_relative.as_posix(), safe="/"),
            base + quote(json_relative.as_posix(), safe="/"))


def training_article(source: Path, messages: dict, root: Path, json_relative: Path) -> tuple[str, str, str]:
    """Return an unescaped plain-text title and excerpt, plus an escaped HTML body."""
    if not isinstance(messages, dict):
        raise ValueError("FIT messages must be a dictionary")
    when = created_at(messages)
    sessions = _rows(messages, "session_mesgs")
    laps = _rows(messages, "lap_mesgs")
    fit_url, json_url = _github_links(source, root, json_relative)
    fit_name = html.escape(Path(source).name, quote=True)
    json_name = html.escape(Path(json_relative).name, quote=True)
    title = f"{_sport_title(sessions)} {when.strftime('%Y-%m-%d %H:%M:%S')} JST"
    excerpt = "FITデータに記録されたセッションとラップの記録。"
    body = [
        "<section>",
        "<h2>元データ</h2>",
        f'<p>FITファイル: <a href="{html.escape(fit_url, quote=True)}">{fit_name}</a><br>',
        f'生成JSON: <a href="{html.escape(json_url, quote=True)}">{json_name}</a></p>',
        f"<p>ファイル作成日時: {html.escape(_time(when), quote=True)}</p>",
        "</section>",
        "<section>",
        "<h2>セッションのサマリー</h2>",
    ]
    if not sessions:
        body.append("<p>セッションの記録はありません。</p>")
    for index, session in enumerate(sessions, 1):
        # Enhanced FIT speed fields supersede the standard speed fields.
        avg_speed = _first_number(session, "enhanced_avg_speed", "avg_speed")
        max_speed = _first_number(session, "enhanced_max_speed", "max_speed")
        fields = [
            ("スポーツ", _text(session.get("sport"))),
            ("サブスポーツ", _text(session.get("sub_sport"))),
            ("開始時刻", _time(session.get("start_time"))),
            ("終了時刻", _time(session.get("timestamp"))),
            ("経過時間", _duration(session.get("total_elapsed_time"))),
            ("運動時間", _duration(session.get("total_timer_time"))),
            ("アクティブ時間", _duration(session.get("active_time"))),
            ("距離", _quantity(session.get("total_distance"), "km", divisor=1000, decimals=3)),
            ("平均心拍", _quantity(session.get("avg_heart_rate"), "bpm")),
            ("最大心拍", _quantity(session.get("max_heart_rate"), "bpm")),
            ("平均速度", _quantity(avg_speed, "km/h", divisor=1 / 3.6)),
            ("最大速度", _quantity(max_speed, "km/h", divisor=1 / 3.6)),
            ("消費カロリー", _quantity(session.get("total_calories"), "kcal")),
            ("累積上昇", _quantity(session.get("total_ascent"), "m")),
            ("累積下降", _quantity(session.get("total_descent"), "m")),
        ]
        if session.get("sport") == "swimming" or any(
                key in session for key in ("pool_length", "num_lengths", "num_active_lengths", "swim_stroke")):
            fields.extend([
                ("泳法", _text(session.get("swim_stroke"))),
                ("プールの長さ", _quantity(session.get("pool_length"), "m")),
                ("プールの総長数", _quantity(_length_count(session.get("num_lengths")), "本", decimals=0)),
                ("アクティブ長数", _quantity(_length_count(session.get("num_active_lengths")), "本", decimals=0)),
                ("総ストローク数", _quantity(session.get("total_strokes"), "回")),
                ("平均SWOLF", _plain_number(session.get("avg_swolf"))),
            ])
        body.append(f"<h3>セッション {index}</h3>")
        body.append('<dl class="main-training-summary">')
        for label, value in fields:
            body.append(f'<div class="main-training-metric"><dt>{html.escape(label, quote=True)}</dt><dd>{html.escape(value, quote=True)}</dd></div>')
        body.append("</dl>")
    body.extend([
        "</section>",
        "<section>",
        "<h2>アクティブ・ラップ別の推移</h2>",
        "<p>強度が active のラップと、プール水泳でアクティブ長数が正のラップを表示します。番号は元のラップ順です。</p>",
    ])
    shown = []
    excluded_intensity = 0
    excluded_no_active_lengths = 0
    unknown_activity = 0
    for index, lap in enumerate(laps, 1):
        intensity = lap.get("intensity")
        if intensity == "active":
            shown.append((index, lap, "active（アクティブ）"))
        elif intensity is None or intensity in ("", "unknown", "invalid"):
            count = _length_count(lap.get("num_active_lengths")) if _is_pool_lap(lap, sessions) else None
            if count is not None and count > 0:
                shown.append((index, lap, "強度未記録（アクティブ長数から活動を判定）"))
            elif count == 0:
                excluded_no_active_lengths += 1
            else:
                unknown_activity += 1
        else:
            # Includes rest; never claim these laps are active.
            excluded_intensity += 1
    if excluded_intensity:
        body.append(f"<p>休息など active 以外の強度が記録されたラップ {excluded_intensity} 件は除外しています。</p>")
    if excluded_no_active_lengths:
        body.append(f"<p>強度未記録でアクティブ長数が 0 のプール水泳ラップ {excluded_no_active_lengths} 件は除外しています。</p>")
    if unknown_activity:
        body.append(f"<p>活動の判定に必要な情報がないラップ {unknown_activity} 件は表から除外しています。</p>")
    if not shown:
        body.append("<p>表示できるラップの記録はありません。</p>")
    else:
        show_swim = any(_is_pool_lap(lap, sessions) or lap.get("sport") == "swimming"
                        or any(key in lap for key in ("swim_stroke", "num_lengths", "num_active_lengths"))
                        for _, lap, _ in shown)
        headers = ["番号", "開始時刻", "経過時間", "運動時間", "距離", "平均心拍", "最大心拍", "平均速度"]
        if show_swim:
            headers.extend(["泳法", "総長数", "アクティブ長数"])
        headers.append("強度・判定根拠")
        body.extend([
            "<table>",
            "<thead><tr>" + "".join(f"<th>{html.escape(header, quote=True)}</th>" for header in headers) + "</tr></thead>",
            "<tbody>",
        ])
        for index, lap, intensity in shown:
            cells = [
                str(index),
                _time(lap.get("start_time")),
                _duration(lap.get("total_elapsed_time")),
                _duration(lap.get("total_timer_time")),
                _quantity(lap.get("total_distance"), "km", divisor=1000, decimals=3),
                _quantity(lap.get("avg_heart_rate"), "bpm"),
                _quantity(lap.get("max_heart_rate"), "bpm"),
                _quantity(_first_number(lap, "enhanced_avg_speed", "avg_speed"), "km/h", divisor=1 / 3.6),
            ]
            if show_swim:
                cells.extend([
                    _text(lap.get("swim_stroke")),
                    _quantity(_length_count(lap.get("num_lengths")), "本", decimals=0),
                    _quantity(_length_count(lap.get("num_active_lengths")), "本", decimals=0),
                ])
            cells.append(intensity)
            body.append("<tr>" + "".join(f"<td>{html.escape(cell, quote=True)}</td>" for cell in cells) + "</tr>")
        body.extend(["</tbody>", "</table>"])
    body.append("</section>")
    return title, excerpt, "\n".join(body)
