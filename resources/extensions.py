"""Constants used throughout SMA-NG: the TMDB API key and sets of recognised file extensions.

tmdb_api_key authenticates requests to The Movie Database API.
valid_poster_extensions lists image formats accepted for artwork downloads.
subtitle_codec_extensions maps FFmpeg subtitle codec names to their file extensions.
bad_post_extensions, bad_post_files, and bad_sub_extensions list files
and extensions that should be skipped during post-processing and subtitle discovery.
"""

valid_poster_extensions = ["jpg", "png"]
tmdb_api_key = "45e408d2851e968e6e4d0353ce621c66"
subtitle_codec_extensions = {"srt": "srt", "webvtt": "vtt", "ass": "ass", "pgs": "sup", "hdmv_pgs_subtitle": "sup", "dvdsub": "mks", "dvb_subtitle": "mks", "dvd_subtitle": "mks"}
bad_post_files = ["resources", ".DS_Store"]
bad_post_extensions = [".txt", ".log", ".pyc", ".md"]
bad_sub_extensions = ["txt", "html", "nfo", "url", "exe", "md", "py", "pyc"]
