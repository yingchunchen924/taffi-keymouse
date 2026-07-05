from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from . import APP_DISPLAY_NAME, APP_VERSION
from .database import Database
from .models import Macro, ReferenceImage
from .paths import EXPORT_DIR, REF_DIR, SCRIPT_DIR
from .utils import normalize_event, safe_name, write_json_atomic


class MacroRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_macro(self, name: str, description: str = "", tags: str = "", folder_id: int = 1) -> Macro:
        base = safe_name(name)
        final = base
        index = 1
        while self.db.get_macro_by_name(final):
            index += 1
            final = f"{base}_{index}"
        macro_id = self.db.create_macro(final, description, tags, folder_id)
        macro = self.db.get_macro(macro_id)
        assert macro is not None
        return macro

    def duplicate_macro(self, macro_id: int) -> Optional[Macro]:
        macro = self.db.get_macro(macro_id)
        if not macro:
            return None
        new_macro = self.create_macro(f"{macro.name}_副本", macro.description, macro.tags, macro.folder_id)
        events = self.db.load_events(macro_id)
        self.db.save_events(new_macro.id, events)
        old_refs = self.db.list_references(macro_id)
        for ref in old_refs:
            src = Path(ref.file_path)
            if src.exists():
                self.import_reference(new_macro.id, src, ref.name)
        return self.db.get_macro(new_macro.id)

    def delete_macro(self, macro_id: int) -> None:
        folder = self.ref_folder(macro_id)
        self.db.delete_macro(macro_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    def save_events(self, macro_id: int, events: List[Dict[str, Any]]) -> None:
        self.db.save_events(macro_id, [normalize_event(evt) for evt in events])
        self.write_json_snapshot(macro_id)

    def load_events(self, macro_id: int) -> List[Dict[str, Any]]:
        return self.db.load_events(macro_id)

    def ref_folder(self, macro_id: int) -> Path:
        folder = REF_DIR / str(macro_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def import_reference(self, macro_id: int, source: Path, preferred_name: Optional[str] = None) -> Optional[ReferenceImage]:
        source = Path(source)
        if not source.exists():
            return None
        folder = self.ref_folder(macro_id)
        name_base = safe_name(Path(preferred_name or source.stem).stem)
        target = folder / f"{name_base}.png"
        index = 1
        while target.exists():
            index += 1
            target = folder / f"{name_base}_{index}.png"
        img = Image.open(source).convert("RGB")
        img.save(target)
        ref_id = self.db.add_reference(macro_id, target.name, str(target), img.width, img.height)
        refs = [r for r in self.db.list_references(macro_id) if r.id == ref_id]
        return refs[0] if refs else self.db.get_reference_by_name(macro_id, target.name)

    def create_reference_from_crop(self, macro_id: int, crop_image: Image.Image, name: str) -> ReferenceImage:
        folder = self.ref_folder(macro_id)
        base = safe_name(Path(name).stem)
        target = folder / f"{base}.png"
        index = 1
        while target.exists():
            index += 1
            target = folder / f"{base}_{index}.png"
        crop_image.convert("RGB").save(target)
        ref_id = self.db.add_reference(macro_id, target.name, str(target), crop_image.width, crop_image.height)
        refs = [r for r in self.db.list_references(macro_id) if r.id == ref_id]
        return refs[0]

    def delete_reference(self, ref: ReferenceImage) -> None:
        path = Path(ref.file_path)
        if path.exists():
            path.unlink()
        self.db.delete_reference(ref.id)

    def reference_path(self, macro_id: int, image_name: str) -> Optional[Path]:
        ref = self.db.get_reference_by_name(macro_id, image_name)
        if not ref:
            return None
        path = Path(ref.file_path)
        return path if path.exists() else None

    def write_json_snapshot(self, macro_id: int) -> Optional[Path]:
        macro = self.db.get_macro(macro_id)
        if not macro:
            return None
        events = self.db.load_events(macro_id)
        refs = self.db.list_references(macro_id)
        payload = {
            "schema": "taffi-suite-v2",
            "app": APP_DISPLAY_NAME,
            "version": APP_VERSION,
            "macro": {
                "name": macro.name,
                "description": macro.description,
                "tags": macro.tags,
                "favorite": macro.favorite,
                "screen_width": macro.screen_width,
                "screen_height": macro.screen_height,
            },
            "events": events,
            "references": [{"name": r.name, "width": r.width, "height": r.height} for r in refs],
        }
        target = SCRIPT_DIR / f"{macro.id}_{safe_name(macro.name)}.json"
        write_json_atomic(target, payload)
        return target

    def export_macro_zip(self, macro_id: int, target_dir: Optional[Path] = None) -> Optional[Path]:
        macro = self.db.get_macro(macro_id)
        if not macro:
            return None
        target_dir = target_dir or EXPORT_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        snapshot = self.write_json_snapshot(macro_id)
        zip_path = target_dir / f"{safe_name(macro.name)}_塔菲导出.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if snapshot:
                zf.write(snapshot, "macro.json")
            for ref in self.db.list_references(macro_id):
                path = Path(ref.file_path)
                if path.exists():
                    zf.write(path, f"refs/{ref.name}")
        return zip_path

    def import_macro_file(self, source: Path) -> Optional[Macro]:
        source = Path(source)
        if not source.exists():
            return None
        if source.suffix.lower() == ".zip":
            return self._import_zip(source)
        payload = json.loads(source.read_text(encoding="utf-8"))
        return self._import_payload(payload, source.parent)

    def _import_zip(self, source: Path) -> Optional[Macro]:
        temp_dir = EXPORT_DIR / "_import_tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(temp_dir)
        macro_json = temp_dir / "macro.json"
        if not macro_json.exists():
            return None
        payload = json.loads(macro_json.read_text(encoding="utf-8"))
        macro = self._import_payload(payload, temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return macro

    def _import_payload(self, payload: Dict[str, Any], base_dir: Path) -> Optional[Macro]:
        if isinstance(payload, list):
            name = "导入脚本"
            events = payload
            refs: List[Dict[str, Any]] = []
            desc = ""
            tags = ""
        else:
            macro_data = payload.get("macro", {})
            name = macro_data.get("name") or payload.get("name") or "导入脚本"
            desc = macro_data.get("description", "")
            tags = macro_data.get("tags", "")
            events = payload.get("events", [])
            refs = payload.get("references", [])
        macro = self.create_macro(name, desc, tags)
        self.save_events(macro.id, [normalize_event(evt) for evt in events if isinstance(evt, dict)])
        refs_dir = base_dir / "refs"
        for ref in refs:
            ref_name = ref.get("name", "") if isinstance(ref, dict) else str(ref)
            path = refs_dir / ref_name
            if path.exists():
                self.import_reference(macro.id, path, ref_name)
        return self.db.get_macro(macro.id)
