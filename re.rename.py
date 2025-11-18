#!/usr/bin/env python3
"""
Batch file‑renamer.

The script:
  * prompts for a folder,
  * asks for a regex pattern that will be matched against each filename,
  * asks for a replacement string (may contain backreferences like \1),
  * shows up to three example renamings,
  * confirms the action, and
  * renames all matching files.

Author: ChatGPT gpt-oss-20b (2025‑11-18)
Prompter: Jahus (@jahus.net)
Prompt: Create a Python script that asks for a folder,
    a matching pattern and a replacement pattern.
    It'll then rename the files in the specified folder
    according to search/replace patterns.
    Before processing, it'll ask the user if sure,
    while showing an example output (3 files max).

"""

import os
import re
import sys


def prompt_folder() -> str:
    """Ask user for a folder that must exist."""
    while True:
        path = input("Enter folder path: ").strip()
        if not path:
            print("  [!] Path cannot be empty.")
            continue

        abs_path = os.path.abspath(path)
        if not os.path.isdir(abs_path):
            print(f"  [!] '{abs_path}' is not a directory.")
            continue
        return abs_path


def prompt_pattern() -> re.Pattern:
    """Ask user for a regular‑expression pattern."""
    while True:
        pat_str = input(
            "Enter regex pattern to match filenames\n.Example: ^(.*)\\.txt$\n"
        ).strip()
        if not pat_str:
            print("  [!] Pattern cannot be empty.")
            continue
        try:
            return re.compile(pat_str)
        except re.error as exc:
            print(f"  [!] Invalid regex: {exc}")


def prompt_replacement() -> str:
    """Ask user for the replacement string."""
    repl = input(
        "Enter replacement string (may contain backreferences like \\1): "
    ).strip()
    return repl


def build_mapping(folder: str, pat: re.Pattern, repl: str) -> dict[str, str]:
    """
    Build a mapping of old full paths to new full paths for files
    that would be renamed.

    Only files (not directories) are considered.
    """
    mapping = {}
    for fname in os.listdir(folder):
        old_path = os.path.join(folder, fname)
        if not os.path.isfile(old_path):
            continue

        try:
            new_name = pat.sub(repl, fname)
        except re.error as exc:
            print(f"  [!] Error applying pattern to '{fname}': {exc}")
            continue

        # Skip files that would keep their name unchanged
        if new_name == fname:
            continue

        new_path = os.path.join(folder, new_name)
        mapping[old_path] = new_path
    return mapping


def show_examples(mapping: dict[str, str]) -> None:
    """Print up to three example renamings."""
    print("\nExample renaming (max 3 files):")
    if not mapping:
        print("  [!] No files will be renamed with the given pattern.\n")
        return

    for i, (old_path, new_path) in enumerate(mapping.items()):
        if i >= 3:
            break
        old_name = os.path.basename(old_path)
        new_name = os.path.basename(new_path)
        print(f"  {old_name} → {new_name}")
    print()


def check_collisions(mapping: dict[str, str]) -> bool:
    """
    Detect collisions that would make the renaming impossible or
    ambiguous. Returns True if a collision is found.
    """
    # Duplicate target names among the mapping itself
    new_names = [os.path.basename(p) for p in mapping.values()]
    duplicates = set([n for n in new_names if new_names.count(n) > 1])

    # Target file already exists on disk (and isn't one of the sources)
    conflicts = []
    for old, new in mapping.items():
        if os.path.exists(new) and not old == new:
            conflicts.append((old, new))

    if duplicates or conflicts:
        print("\n[!] Potential collision detected:")
        if duplicates:
            print(f"  Duplicate target names among changes: {', '.join(duplicates)}")
        if conflicts:
            for o, n in conflicts:
                print(f"  Target '{os.path.basename(n)}' already exists and "
                      f"is not one of the files to rename.")
        return True
    return False


def rename_files(mapping: dict[str, str]) -> None:
    """Perform the actual renaming."""
    print("\nRenaming files:")
    for old_path, new_path in mapping.items():
        try:
            os.rename(old_path, new_path)
            print(f"  {os.path.basename(old_path)} → {os.path.basename(new_path)}")
        except Exception as exc:
            print(f"  [!] Error renaming '{old_path}' to '{new_path}': {exc}")
    print("\n[✓] Renaming completed.")


def main() -> None:
    folder = prompt_folder()
    pat = prompt_pattern()
    repl = prompt_replacement()

    mapping = build_mapping(folder, pat, repl)
    show_examples(mapping)

    if not mapping:
        sys.exit(0)

    if check_collisions(mapping):
        proceed = input("Proceed despite collisions? (y/N): ").strip().lower()
        if proceed != 'y':
            print("\n[!] Aborted by user.")
            sys.exit(0)

    confirm = input("Proceed with renaming? (y/N): ").strip().lower()
    if confirm != 'y':
        print("\n[!] Aborted by user.")
        sys.exit(0)

    rename_files(mapping)


if __name__ == "__main__":
    main()
