-- Local cache path for each preview clip (embedders read from disk, not the URL).
ALTER TABLE audio_clips ADD COLUMN local_path TEXT;
