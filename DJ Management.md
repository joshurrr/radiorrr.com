# Radio RRR — DJ Management SOP

### Add a DJ

```bash
djadd @username
```

Adds a new DJ to the database. If the DJ was previously disabled, this **re-enables** them.

Multiple DJs can be added at once:

```bash
djadd @djone @djtwo @djthree
```

### Disable a DJ

```bash
djdis @username
```

Disables the DJ from the active RRR system but **keeps their database/profile data**.

### Permanently delete a DJ

```bash
djdel @username
```

Permanently deletes the DJ's database record. You must type **DELETE** to confirm.

### Help

```bash
djadd -h
djdis -h
djdel -h
```

### Quick reference

| Command | Action |
|---|---|
| `djadd` | Add / re-enable |
| `djdis` | Disable / keep data |
| `djdel` | Permanently delete |