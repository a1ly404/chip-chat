# Skills catalog (parity P1.3)

A skill is a markdown file with a short front-matter header saying what it is and when to use it.

```markdown
---
name: Disk cleanup
description: When a disk usage alert fires, find large files before deleting anything.
triggers: [disk full, disk usage]
version: 1
---
# Steps
1. ...
```

## Sources

- **Pack skills** come from the active system pack's `skill_bodies_dir` surface and are read-only.
  Older pack bodies without front-matter still load: the first `# heading` becomes the name and the
  first prose line becomes the description.
- **User skills** live in `CHIP_CHAT_DATA_DIR/skills/<id>.md` and are written with `chip skill add`.

The id is the file name without `.md` (lowercase letters, digits, dashes). A user skill cannot shadow
a pack skill with the same id unless it is added with `--override`. Invalid files and blocked shadows
are skipped, and `chip skill list --skipped` shows why. Secret-shaped text is refused on add, using the
same scanner as durable memory.

## CLI

```bash
chip skill list [--skipped]
chip skill show <id>
chip skill search "<words>" [--top-k 5]
chip skill add <id> --name "..." --description "..." (--body "..." | --body-file path) [--trigger "..."]... [--override] [--replace]
chip skill delete <id>          # user skills only
```

## Library

`chip.skills.select(catalog, text)` picks skills whose trigger phrase appears in the text (word
boundaries), falling back to word overlap with name, description and triggers.
`chip.skills.injection_block(skills, budget_chars=...)` renders bodies for a turn and never exceeds
the character budget (the first skill that does not fit is truncated with a marker, later ones dropped).

Nothing injects skills into turns yet. Turn injection will be gated by `CHIP_SKILLS=1` (default off).
