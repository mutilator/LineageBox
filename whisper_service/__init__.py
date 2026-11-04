"""Speech to text for the catalog's videos: LineageBox's worker POSTs each video's audio here.

Not part of the LineageBox package or its Docker image: it runs on a PC with a GPU, often next to Ollama (start.ps1 on
Windows), or in a container of its own, on the processor (the Dockerfile in this folder, compose.whisper.yaml).
"""
