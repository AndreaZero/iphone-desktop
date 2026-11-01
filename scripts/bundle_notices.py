"""Keep the licenses of installed dependencies alongside the portable build."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "iPhone Desktop" / "licenses"
destination.mkdir(parents=True, exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
for package in distributions():
    for entry in package.files or ():
        name = str(entry).replace("\\", "/")
        if ".dist-info/" not in name:
            continue
        relative = name.split(".dist-info/", 1)[1]
        if not (relative.lower().startswith("licenses/") or Path(relative).name.lower().startswith(("license", "copying", "notice"))):
            continue
        original = Path(package.locate_file(entry))
        if original.is_file():
            target = destination / f"{package.metadata['Name']}-{package.version}" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
print(f"Licenses: {destination}")
