# curatr

curatr is a Kodi 21 add-on for creating and maintaining personalised movie and TV show lists.

## Features

- Natural-language list creation
- Keyword Matching without an AI request
- Editable keyword filters in the list form, retained through preview and creation
- Optional OpenAI, Gemini, Anthropic, OpenRouter and compatible AI services
- Movies, TV shows or mixed lists
- Kodi Library and optional Trakt preference history
- Optional Trakt list syncing
- Custom list artwork and widget folders
- Coordinated white genre icons, soft gradient colours and dark grey monochrome fanart
- Matching menu icons with separate square and landscape widget artwork
- Unified controller-friendly list and folder management
- A single live-preview artwork editor for list and folder icons and fanart
- Folder contents that can be arranged before a new folder is saved
- Four coordinated window themes with optional custom colours and light mode
- Persistent active-tab highlights and theme-aware keyword controls
- Native Kodi cast, crew, title and rating metadata from TMDB
- Optional cached IMDb, Rotten Tomatoes and Metacritic ratings through MDBList
- Kodi Library-first playback with optional compatible video add-ons
- Configurable Kodi context-menu actions for titles and add-on folders
- Temporary Find Similar poster previews using Keyword Matching or AI
- Local backup, restore and recovery snapshots

## Installation

Install the release ZIP through **Kodi → Add-ons → Install from ZIP file**. Kodi 21 Omega or newer is recommended.

## Setup

Open curatr Settings and add only the services you want to use. TMDB is required for Keyword Matching and title metadata. Trakt and AI services are optional.

Playback is automatic by default. Titles in the Kodi Library use Kodi's built-in player. Other titles can use a compatible installed video add-on after running **Set Up Installed Video Add-ons** under Playback.

## Privacy

Settings, cached metadata, lists and API credentials are stored in Kodi's local add-on profile. curatr sends requests only to services the user enables. API credentials and Trakt tokens are excluded from curatr backups.

## License

MIT. See [LICENSE.txt](LICENSE.txt).

## Development

The source ZIP includes the prebuilt artwork, editable geometry and release
checks. See [ARTWORK.md](ARTWORK.md) for build and validation commands. These
checks use local Kodi stubs; testing on Android and Xbox remains necessary.
