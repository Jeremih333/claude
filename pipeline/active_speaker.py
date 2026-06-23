from __future__ import annotations

import math
from statistics import mean

import numpy as np
from moviepy import VideoFileClip

# PHASE 5: Face detection cache for overlapping candidates
_FACE_TRACK_CACHE = {}

def _cache_key(video_path: str, start: float, end: float, fps: int, profile: str) -> str:
    """Generate cache key for face track results."""
    return f"{video_path}:{start:.2f}-{end:.2f}:{fps}:{profile}"

def clear_face_track_cache():
    """Clear face track cache at episode boundaries."""
    global _FACE_TRACK_CACHE
    _FACE_TRACK_CACHE.clear()


def _build_mediapipe_detector(detector_profile="light"):
    try:
        import mediapipe as mp
        strong = str(detector_profile or "light").lower() in {"strong", "final_clip_strong", "refine"}
        # PHASE 5: Reduced thresholds to catch more faces (side profiles, poor lighting)
        model0_conf = 0.12 if strong else 0.22  # Was 0.18/0.35
        model1_conf = 0.10 if strong else 0.20  # Was 0.14/0.28
        return [
            mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=model0_conf
            ),
            mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=model1_conf
            ),
        ]
    except Exception:
        return None


def _build_person_detector(detector_profile="light"):
    try:
        import cv2

        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        return {"hog": hog, "profile": str(detector_profile or "light").lower()}
    except Exception:
        return None


def _scene_change_score(frame, previous_small=None):
    try:
        import cv2

        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        small = cv2.resize(gray, (48, 27), interpolation=cv2.INTER_AREA)
        if previous_small is None:
            return small, 0.0, False
        diff = np.mean(np.abs(small.astype(np.float32) - previous_small.astype(np.float32))) / 255.0
        motion_score = float(max(0.0, min(1.0, diff * 2.4)))
        cut_like = motion_score >= 0.18
        return small, motion_score, cut_like
    except Exception:
        return previous_small, 0.0, False


def _clamp01(value):
    return float(max(0.0, min(1.0, value)))


def _normalize_face(face, frame_w, frame_h):
    cx, cy, bw, bh = face
    return {
        "center_x": _clamp01(cx),
        "center_y": _clamp01(cy),
        "box_w": float(max(0.05, min(1.0, bw))),
        "box_h": float(max(0.05, min(1.0, bh))),
    }


def _crop_lower_face_region(frame, face):
    frame_h, frame_w = frame.shape[:2]
    cx = int(float(face["center_x"]) * frame_w)
    cy = int(float(face["center_y"]) * frame_h)
    bw = max(8, int(float(face["box_w"]) * frame_w))
    bh = max(8, int(float(face["box_h"]) * frame_h))
    x1 = max(0, cx - bw // 2)
    x2 = min(frame_w, cx + bw // 2)
    y1 = max(0, cy - bh // 2)
    y2 = min(frame_h, cy + bh // 2)
    if x2 <= x1 or y2 <= y1:
        return None
    mouth_y1 = y1 + int((y2 - y1) * 0.55)
    region = frame[mouth_y1:y2, x1:x2]
    if region.size == 0:
        return None
    try:
        gray = np.mean(region.astype(np.float32), axis=2)
    except Exception:
        return None
    return gray


def _face_motion_score(face, frame, previous_crops):
    track_id = int(face.get("track_id", -1))
    crop = _crop_lower_face_region(frame, face)
    if crop is None:
        return 0.0
    prev = previous_crops.get(track_id)
    previous_crops[track_id] = crop
    if prev is None:
        return 0.0
    h = min(prev.shape[0], crop.shape[0])
    w = min(prev.shape[1], crop.shape[1])
    if h <= 3 or w <= 3:
        return 0.0
    diff = np.abs(crop[:h, :w] - prev[:h, :w])
    score = float(np.mean(diff) / 48.0)
    return _clamp01(score)


def _track_stats(track):
    seen = int(track.get("seen_count", 0))
    miss = int(track.get("missed", 0))
    size = float(track.get("box_w", 0.12)) * float(track.get("box_h", 0.18))
    visibility = _clamp01(0.35 + min(0.55, seen * 0.09) - miss * 0.12 + min(0.10, size * 1.5))
    stability = _clamp01(min(1.0, seen / 6.0) - miss * 0.08)
    last_speaking = float(track.get("last_speaking_prob", 0.0))
    last_listener = float(track.get("last_listener_prob", 0.0))
    return visibility, stability, last_speaking, last_listener


def _evidence_scores(face, track, previous_primary_track_id=None, face_count=1, scene_change_score=0.0):
    visibility_score, track_stability_score, last_speaking, last_listener = _track_stats(track)
    area = float(face["box_w"]) * float(face["box_h"])
    size_score = _clamp01(math.sqrt(max(0.0, area)) / 0.24)
    mouth_motion_proxy = float(face.get("mouth_motion_proxy", 0.0))
    subtitle_turn_alignment_score = 0.42 + min(0.38, mouth_motion_proxy * 0.62)
    dialogue_context_score = 0.18 if face_count >= 2 else 0.0
    previous_anchor_continuity_bonus = 0.12 if previous_primary_track_id is not None and int(face.get("track_id", -1)) == int(previous_primary_track_id) else 0.0
    scene_change_consistency = _clamp01(1.0 - min(1.0, float(scene_change_score) / 0.35))
    speaking_score = (
        visibility_score * 0.20
        + track_stability_score * 0.14
        + size_score * 0.10
        + mouth_motion_proxy * 0.34
        + subtitle_turn_alignment_score * 0.10
        + dialogue_context_score * 0.05
        + previous_anchor_continuity_bonus * 0.55
        + last_speaking * 0.07
    )
    listener_score = (
        visibility_score * 0.28
        + track_stability_score * 0.22
        + size_score * 0.12
        + (0.18 if face_count >= 2 else 0.04)
        + last_listener * 0.12
        + previous_anchor_continuity_bonus * 0.35
    )
    if mouth_motion_proxy <= 0.04 and face_count >= 2:
        listener_score += 0.08
    lock_confidence = (
        _clamp01(speaking_score) * 0.32
        + track_stability_score * 0.20
        + visibility_score * 0.18
        + previous_anchor_continuity_bonus * 0.08
        + dialogue_context_score * 0.10
        + scene_change_consistency * 0.06
    )
    evidence = {
        "face_visibility_score": round(visibility_score, 4),
        "track_stability_score": round(track_stability_score, 4),
        "mouth_motion_proxy": round(mouth_motion_proxy, 4),
        "subtitle_turn_alignment_score": round(subtitle_turn_alignment_score, 4),
        "dialogue_context_score": round(dialogue_context_score, 4),
        "previous_anchor_continuity_bonus": round(previous_anchor_continuity_bonus, 4),
        "scene_change_consistency": round(scene_change_consistency, 4),
        "speaking_score": round(_clamp01(speaking_score), 4),
        "listener_score": round(_clamp01(listener_score), 4),
        "lock_confidence": round(_clamp01(lock_confidence), 4),
    }
    return evidence


def _detect_faces(frame, detector):
    faces = []
    try:
        detectors = detector if isinstance(detector, (list, tuple)) else ([detector] if detector is not None else [])
        for current_detector in detectors:
            try:
                result = current_detector.process(frame)
            except Exception:
                continue
            detections = getattr(result, "detections", None) or []
            for det in detections:
                bbox = det.location_data.relative_bounding_box
                faces.append(
                    (
                        bbox.xmin + bbox.width / 2.0,
                        bbox.ymin + bbox.height / 2.0,
                        bbox.width,
                        bbox.height,
                    )
                )
        if faces:
            merged = []
            for face in faces:
                cx, cy, bw, bh = face
                too_close = False
                for index, existing in enumerate(merged):
                    ecx, ecy, ebw, ebh = existing
                    if abs(cx - ecx) < 0.08 and abs(cy - ecy) < 0.10:
                        if bw * bh > ebw * ebh:
                            merged[index] = face
                        too_close = True
                        break
                if not too_close:
                    merged.append(face)
            return merged, True
    except Exception:
        pass

    try:
        import cv2

        gray = cv2.cvtColor(frame[:, :, ::-1], cv2.COLOR_BGR2GRAY)
        fh, fw = frame.shape[:2]
        cascade_paths = [
            ("haarcascade_frontalface_default.xml", 1.05, 2),
            ("haarcascade_frontalface_alt2.xml", 1.03, 1),
            ("haarcascade_frontalface_alt.xml", 1.03, 1),
            ("haarcascade_profileface.xml", 1.03, 1),
        ]
        for cascade_name, scale_factor, min_neighbors in cascade_paths:
            try:
                cascade = cv2.CascadeClassifier(cv2.data.haarcascades + cascade_name)
                faces = cascade.detectMultiScale(gray, scale_factor, min_neighbors)
            except Exception:
                faces = []
            if len(faces) > 0:
                normalized = []
                for x, y, w, h in faces:
                    normalized.append(((x + w / 2.0) / fw, (y + h / 2.0) / fh, w / fw, h / fh))
                return normalized, True
        upscaled = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        for cascade_name, scale_factor, min_neighbors in [
            ("haarcascade_frontalface_default.xml", 1.03, 1),
            ("haarcascade_frontalface_alt2.xml", 1.02, 1),
            ("haarcascade_frontalface_alt.xml", 1.02, 1),
        ]:
            try:
                cascade = cv2.CascadeClassifier(cv2.data.haarcascades + cascade_name)
                faces = cascade.detectMultiScale(upscaled, scale_factor, min_neighbors)
            except Exception:
                faces = []
            if len(faces) > 0:
                normalized = []
                for x, y, w, h in faces:
                    normalized.append(((x + w / 2.0) / (fw * 1.5), (y + h / 2.0) / (fh * 1.5), w / (fw * 1.5), h / (fh * 1.5)))
                return normalized, True
    except Exception:
        pass

    return [], False


def _detect_people(frame, detector):
    if detector is None:
        return []
    try:
        import cv2

        detector_profile = str((detector or {}).get("profile", "light"))
        hog = (detector or {}).get("hog")
        if hog is None:
            return []
        rgb = frame.astype(np.uint8)
        frame_h, frame_w = rgb.shape[:2]
        scale = min(1.0, 480.0 / max(frame_h, frame_w))
        if scale < 1.0:
            resized = cv2.resize(rgb, (int(frame_w * scale), int(frame_h * scale)), interpolation=cv2.INTER_AREA)
        else:
            resized = rgb
        boxes, weights = hog.detectMultiScale(
            resized,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
        )
        persons = []
        for (x, y, w, h), weight in zip(boxes, weights):
            confidence = float(weight[0] if isinstance(weight, (tuple, list, np.ndarray)) else weight)
            min_confidence = 0.12 if detector_profile in {"strong", "final_clip_strong", "refine"} else 0.2
            if confidence < min_confidence:
                continue
            if scale < 1.0:
                x = int(x / scale)
                y = int(y / scale)
                w = int(w / scale)
                h = int(h / scale)
            persons.append(
                {
                    "center_x": _clamp01((x + w / 2.0) / max(1, frame_w)),
                    "center_y": _clamp01((y + h / 2.0) / max(1, frame_h)),
                    "box_w": _clamp01(w / max(1, frame_w)),
                    "box_h": _clamp01(h / max(1, frame_h)),
                    "confidence": _clamp01(confidence / 1.5),
                }
            )
        persons.sort(
            key=lambda item: (item["confidence"], item["box_w"] * item["box_h"]),
            reverse=True,
        )
        return persons[:2]
    except Exception:
        return []


def _assign_track_ids(faces, active_tracks, next_track_id, max_distance=0.18):
    assigned = []
    used_track_ids = set()
    for face in faces:
        best_track_id = None
        best_distance = None
        for track_id, track in active_tracks.items():
            if track_id in used_track_ids:
                continue
            dx = face["center_x"] - track["center_x"]
            dy = face["center_y"] - track["center_y"]
            size_penalty = abs((face["box_w"] * face["box_h"]) - (track["box_w"] * track["box_h"]))
            distance = (dx * dx + dy * dy) ** 0.5 + size_penalty * 0.35
            if distance <= max_distance and (best_distance is None or distance < best_distance):
                best_track_id = track_id
                best_distance = distance
        if best_track_id is None:
            best_track_id = next_track_id
            next_track_id += 1
        used_track_ids.add(best_track_id)
        previous = active_tracks.get(best_track_id, {})
        active_tracks[best_track_id] = {
            **previous,
            **face,
            "missed": 0,
            "seen_count": int(previous.get("seen_count", 0)) + 1,
        }
        assigned.append({**face, "track_id": int(best_track_id), "detected": True})

    missing = []
    for track_id, track in list(active_tracks.items()):
        if track_id not in used_track_ids:
            track["missed"] = int(track.get("missed", 0)) + 1
            # PHASE 5: Increased persistence from 5 to 12 frames (4s at 3fps) for better stability
            if track["missed"] > 12:
                active_tracks.pop(track_id, None)
            else:
                missing.append(
                    {
                        "track_id": int(track_id),
                        "center_x": track["center_x"],
                        "center_y": track["center_y"],
                        "box_w": track["box_w"],
                        "box_h": track["box_h"],
                        "detected": False,
                    }
                )
    return assigned, missing, next_track_id


def _pick_primary_face(faces, previous_primary_track_id=None):
    detected = [item for item in faces if item.get("detected")]
    if not detected:
        return None
    def _face_priority(item):
        return (
            float(item.get("speaking_score", 0.0)) * 1.05
            + float(item.get("mouth_motion_proxy", 0.0)) * 0.42
            + float(item.get("listener_score", 0.0)) * 0.20
            + float(item["box_w"] * item["box_h"]) * 0.36
        )

    best = max(detected, key=_face_priority)
    if previous_primary_track_id is not None:
        previous_face = None
        for face in detected:
            if int(face.get("track_id", -1)) == int(previous_primary_track_id):
                previous_face = face
                break
        if previous_face is not None and int(previous_face.get("track_id", -1)) != int(best.get("track_id", -1)):
            prev_score = _face_priority(previous_face) + 0.08
            best_score = _face_priority(best)
            speaking_gap = float(best.get("speaking_score", 0.0)) - float(previous_face.get("speaking_score", 0.0))
            motion_gap = float(best.get("mouth_motion_proxy", 0.0)) - float(previous_face.get("mouth_motion_proxy", 0.0))
            if prev_score >= best_score - 0.02 and speaking_gap < 0.04 and motion_gap < 0.05:
                return previous_face
            if speaking_gap >= 0.04 or motion_gap >= 0.05 or best_score >= prev_score + 0.02:
                return best
            return previous_face
    return best


def _recent_face_candidates(frame_faces, active_tracks, max_missed=2):
    recent = []
    for face in frame_faces:
        track_id = int(face.get("track_id", -1))
        track = active_tracks.get(track_id, {})
        missed = int(track.get("missed", 99))
        if face.get("detected") or missed <= int(max_missed):
            recent.append({**face, "recent_hold": not bool(face.get("detected"))})
    recent.sort(
        key=lambda item: (
            not item.get("detected", False),
            -float(item.get("speaking_score", 0.0)),
            -float(item.get("listener_score", 0.0)),
            -(float(item.get("box_w", 0.0)) * float(item.get("box_h", 0.0))),
        )
    )
    return recent


def _pick_face_hold_candidate(recent_faces, previous_primary_track_id=None):
    if not recent_faces:
        return None
    if previous_primary_track_id is not None:
        for face in recent_faces:
            if int(face.get("track_id", -1)) == int(previous_primary_track_id):
                return face
    return max(
        recent_faces,
        key=lambda item: (
            float(item.get("listener_score", 0.0)),
            float(item.get("speaking_score", 0.0)),
            float(item.get("box_w", 0.0)) * float(item.get("box_h", 0.0)),
        ),
    )


def estimate_face_tracks(video_path, start, end, sample_fps=2, detector_profile="light"):
    tracks = []
    strong_profile = str(detector_profile or "light").lower() in {"strong", "final_clip_strong", "refine"}
    detector = _build_mediapipe_detector(detector_profile=detector_profile)
    person_detector = _build_person_detector(detector_profile=detector_profile)
    active_tracks = {}
    previous_crops = {}
    previous_scene_small = None
    next_track_id = 1
    previous_primary_track_id = None
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration or 0.0
        start_t = max(0.0, float(start or 0.0))
        end_t = min(duration, float(end) if end is not None else duration)
        if end_t <= start_t:
            end_t = duration
        effective_fps = max(1.0, float(sample_fps))
        if strong_profile:
            effective_fps = max(effective_fps, 6.0)
        step = 1.0 / effective_fps
        t = start_t
        while t < end_t and t < duration:
            try:
                frame = clip.get_frame(t)
            except Exception:
                break
            previous_scene_small, scene_change_score, scene_change_detected = _scene_change_score(
                frame,
                previous_small=previous_scene_small,
            )
            if scene_change_detected:
                previous_crops.clear()
                for track in active_tracks.values():
                    track["missed"] = min(3, int(track.get("missed", 0)) + 1)
                    track["last_speaking_prob"] = float(track.get("last_speaking_prob", 0.0)) * 0.72
                    track["last_listener_prob"] = float(track.get("last_listener_prob", 0.0)) * 0.82
            frame_h, frame_w = frame.shape[:2]
            detected_faces, detected = _detect_faces(frame, detector)
            normalized_faces = [_normalize_face(face, frame_w, frame_h) for face in detected_faces]
            assigned_faces, missing_faces, next_track_id = _assign_track_ids(
                normalized_faces,
                active_tracks,
                next_track_id,
            )
            persons = _detect_people(frame, person_detector) if strong_profile or len(assigned_faces) <= 2 else []
            face_count = len(assigned_faces)
            for face in assigned_faces:
                face["mouth_motion_proxy"] = _face_motion_score(face, frame, previous_crops)
                track = active_tracks.get(int(face["track_id"]), {})
                face.update(
                    _evidence_scores(
                        face,
                        track,
                        previous_primary_track_id=previous_primary_track_id,
                        face_count=face_count,
                        scene_change_score=scene_change_score,
                    )
                )
                track["last_speaking_prob"] = float(face.get("speaking_score", 0.0))
                track["last_listener_prob"] = float(face.get("listener_score", 0.0))
                active_tracks[int(face["track_id"])] = track
            frame_faces = sorted(
                assigned_faces + missing_faces,
                key=lambda item: (
                    not item.get("detected", False),
                    -float(item.get("speaking_score", 0.0)),
                    -(item["box_w"] * item["box_h"]),
                ),
            )
            recent_faces = _recent_face_candidates(frame_faces, active_tracks, max_missed=2)
            primary = _pick_primary_face(frame_faces, previous_primary_track_id=previous_primary_track_id)
            primary_person = persons[0] if persons else None
            face_hold_candidate = _pick_face_hold_candidate(recent_faces, previous_primary_track_id=previous_primary_track_id)
            if primary is not None:
                primary_center_x = float(primary["center_x"])
                primary_center_y = float(primary["center_y"])
                primary_box_w = float(primary["box_w"])
                primary_box_h = float(primary["box_h"])
                primary_detected = bool(primary.get("detected", detected))
                primary_track_id = int(primary.get("track_id", -1))
                primary_source = "face"
                previous_primary_track_id = primary_track_id
            elif primary_person is not None:
                primary_center_x = float(primary_person["center_x"])
                primary_center_y = float(primary_person["center_y"])
                primary_box_w = float(primary_person["box_w"])
                primary_box_h = float(primary_person["box_h"])
                primary_detected = False
                primary_track_id = -1
                primary_source = "person"
                previous_primary_track_id = None
            elif face_hold_candidate is not None:
                primary_center_x = float(face_hold_candidate.get("center_x", 0.5))
                primary_center_y = float(face_hold_candidate.get("center_y", 0.5))
                primary_box_w = float(face_hold_candidate.get("box_w", 0.22))
                primary_box_h = float(face_hold_candidate.get("box_h", 0.32))
                primary_detected = False
                primary_track_id = int(face_hold_candidate.get("track_id", -1))
                primary_source = "face_hold"
                previous_primary_track_id = primary_track_id if primary_track_id >= 0 else previous_primary_track_id
            else:
                primary_center_x = 0.5
                primary_center_y = 0.5
                primary_box_w = 0.25
                primary_box_h = 0.35
                primary_detected = False
                primary_track_id = -1
                primary_source = "none"
                previous_primary_track_id = None
            visible_faces = [face for face in frame_faces if face.get("detected")]
            speaking_scores = sorted(
                (float(face.get("speaking_score", 0.0)) for face in recent_faces),
                reverse=True,
            )
            listener_scores = sorted(
                (float(face.get("listener_score", 0.0)) for face in recent_faces),
                reverse=True,
            )
            top_speaking = speaking_scores[0] if speaking_scores else 0.0
            second_speaking = speaking_scores[1] if len(speaking_scores) > 1 else 0.0
            top_listener = listener_scores[0] if listener_scores else 0.0
            dialogue_balance = 0.0
            if len(recent_faces) >= 2:
                dialogue_balance = max(0.0, 1.0 - min(1.0, abs(top_speaking - second_speaking) * 1.6))
                if top_listener >= 0.24:
                    dialogue_balance = max(dialogue_balance, min(1.0, top_listener + 0.16))
            elif len(recent_faces) == 1 and top_listener >= 0.24 and top_speaking >= 0.16:
                dialogue_balance = min(0.28, top_listener * 0.45 + top_speaking * 0.2)
            evidence_summary = {
                "primary_track_id": int(primary_track_id),
                "primary_source": primary_source,
                "primary_speaking_score": round(float(primary.get("speaking_score", 0.0)) if primary is not None else 0.0, 4),
                "primary_listener_score": round(float(primary.get("listener_score", 0.0)) if primary is not None else 0.0, 4),
                "lock_confidence": round(float(primary.get("lock_confidence", 0.0)) if primary is not None else 0.0, 4),
                "visible_faces": len(visible_faces),
                "recent_face_memory_count": len(recent_faces),
                "face_hold_available": bool(any(not face.get("detected") for face in recent_faces)),
                "visible_persons": len(persons),
                "scene_change_score": round(float(scene_change_score), 4),
                "scene_change_detected": bool(scene_change_detected),
                "dialogue_scene_likelihood": round(
                    _clamp01(
                        (0.40 if len(recent_faces) >= 2 else 0.0)
                        + dialogue_balance * 0.50
                        + min(0.14, len(persons) * 0.06)
                    ),
                    4,
                ),
                "top_tracks": [
                    {
                        "track_id": int(face.get("track_id", -1)),
                        "speaking_score": round(float(face.get("speaking_score", 0.0)), 4),
                        "listener_score": round(float(face.get("listener_score", 0.0)), 4),
                        "lock_confidence": round(float(face.get("lock_confidence", 0.0)), 4),
                        "mouth_motion_proxy": round(float(face.get("mouth_motion_proxy", 0.0)), 4),
                    }
                    for face in recent_faces[:3]
                ],
            }
            tracks.append(
                {
                    "t": round(t, 3),
                    "center_x": primary_center_x,
                    "center_y": primary_center_y,
                    "box_w": primary_box_w,
                    "box_h": primary_box_h,
                    "detected": primary_detected,
                    "track_id": primary_track_id,
                    "primary_source": primary_source,
                    "no_subject_detected": primary_source == "none",
                    "faces": frame_faces,
                    "persons": persons,
                    "speaker_evidence_summary": evidence_summary,
                    "target_selection_mode": "evidence_scored",
                    "scene_change_score": round(float(scene_change_score), 4),
                    "scene_change_detected": bool(scene_change_detected),
                    "subject_detector_pass": "strong" if strong_profile else "light",
                }
            )
            t += step
        try:
            clip.close()
        except Exception:
            pass
    except Exception:
        tracks = []
    finally:
        if detector is not None:
            for item in detector if isinstance(detector, (list, tuple)) else [detector]:
                try:
                    item.close()
                except Exception:
                    pass

    if not tracks:
        tracks = [
            {
                "t": float(start or 0.0),
                "center_x": 0.5,
                "center_y": 0.5,
                "box_w": 0.25,
                "box_h": 0.35,
                "detected": False,
                "track_id": -1,
                "primary_source": "none",
                "no_subject_detected": True,
                "faces": [],
                "persons": [],
                    "speaker_evidence_summary": {
                        "primary_track_id": -1,
                        "primary_source": "none",
                        "primary_speaking_score": 0.0,
                        "primary_listener_score": 0.0,
                        "lock_confidence": 0.0,
                        "visible_faces": 0,
                        "recent_face_memory_count": 0,
                        "face_hold_available": False,
                        "visible_persons": 0,
                        "scene_change_score": 0.0,
                        "scene_change_detected": False,
                    "dialogue_scene_likelihood": 0.0,
                    "top_tracks": [],
                },
                "scene_change_score": 0.0,
                "scene_change_detected": False,
                "subject_detector_pass": "strong" if strong_profile else "light",
            }
        ]
    return tracks


def estimate_face_centers(video_path, start, end, sample_fps=2, detector_profile="light"):
    return [
        (item["t"], item["center_x"], item["center_y"])
        for item in estimate_face_tracks(video_path, start, end, sample_fps=sample_fps, detector_profile=detector_profile)
    ]


def estimate_active_speaker_bboxes(video_path, start, end, sample_fps=2, detector_profile="light"):
    bboxes = []
    for item in estimate_face_tracks(video_path, start, end, sample_fps=sample_fps, detector_profile=detector_profile):
        cx = item["center_x"]
        cy = item["center_y"]
        bw = item["box_w"]
        bh = item["box_h"]
        bboxes.append(
            (
                item["t"],
                max(0.0, cx - bw / 2.0),
                max(0.0, cy - bh / 2.0),
                min(1.0, cx + bw / 2.0),
                min(1.0, cy + bh / 2.0),
            )
        )
    return bboxes


def sample_face_focus_stats(video_path, start, end, sample_fps=1, detector_profile="light"):
    tracks = estimate_face_tracks(video_path, start, end, sample_fps=sample_fps, detector_profile=detector_profile)
    detected = [item for item in tracks if item["detected"]]
    person_detected = [item for item in tracks if item.get("persons")]
    centers = [item["center_x"] for item in tracks]
    centers_y = [item["center_y"] for item in tracks]
    person_sizes = []
    scene_change_scores = []
    scene_change_detected = 0
    for item in tracks:
        for person in item.get("persons", []) or []:
            person_sizes.append(float(person["box_w"]) * float(person["box_h"]))
        scene_change_scores.append(float(item.get("scene_change_score", 0.0) or 0.0))
        if bool(item.get("scene_change_detected")):
            scene_change_detected += 1
    subject_presence = max(
        len(detected) / max(1, len(tracks)),
        (len(person_detected) / max(1, len(tracks))) * 0.88,
    )
    return {
        "face_presence": round(len(detected) / max(1, len(tracks)), 4),
        "person_presence": round(len(person_detected) / max(1, len(tracks)), 4),
        "subject_presence": round(subject_presence, 4),
        "avg_face_size": round(
            mean([item["box_w"] * item["box_h"] for item in detected]) if detected else 0.0,
            4,
        ),
        "avg_person_size": round(mean(person_sizes) if person_sizes else 0.0, 4),
        "avg_scene_change_score": round(mean(scene_change_scores) if scene_change_scores else 0.0, 4),
        "scene_change_detected_count": int(scene_change_detected),
        "avg_center_x": round(mean(centers), 4) if centers else 0.5,
        "avg_center_y": round(mean(centers_y), 4) if centers_y else 0.5,
        "subject_detector_pass": "strong" if str(detector_profile or "light").lower() in {"strong", "final_clip_strong", "refine"} else "light",
    }
