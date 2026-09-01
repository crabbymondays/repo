# curatr

curatr is a Kodi 21 add-on for creating and maintaining personalised movie and TV show lists.

## Features

- Natural-language list creation
- Keyword Matching without an AI request
- Optional OpenAI, Gemini, Anthropic, OpenRouter and compatible AI services
- Movies, TV shows or mixed lists
- Kodi Library and optional Trakt preference history
- Optional Trakt list syncing
- Custom list artwork and widget folders
- Native Kodi cast, crew and title metadata from TMDB
- Kodi Library-first playback with optional compatible video add-ons
- Configurable Kodi context-menu actions for titles and add-on folders
- Temporary Find Similar poster previews using Keyword Matching or AI
- Local backup and restore

## Installation

Install the release ZIP through **Kodi → Add-ons → Install from ZIP file**. Kodi 21 Omega or newer is recommended.

## Setup

Open curatr Settings and add only the services you want to use. TMDB is required for Keyword Matching and title metadata. Trakt and AI services are optional.

Playback is automatic by default. Titles in the Kodi Library use Kodi's built-in player. Other titles can use a compatible installed video add-on after running **Set Up Installed Video Add-ons** under Playback.

## Privacy

Settings, cached metadata, lists and API credentials are stored in Kodi's local add-on profile. curatr sends requests only to services the user enables. API credentials and Trakt tokens are excluded from curatr backups.

## License

MIT. See [LICENSE.txt](LICENSE.txt).
