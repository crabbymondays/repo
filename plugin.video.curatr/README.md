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
- 21 coordinated genre and general artwork symbols, plus blank icon and fanart choices
- 25 shared colours in rainbow order for artwork, menu backgrounds and window accents
- Font Awesome and Curatr menu icons with consistent padding and separate landscape widget artwork
- Unified controller-friendly list and folder management
- A single live-preview artwork editor for list and folder icons and fanart
- Folder contents that can be arranged before a new folder is saved
- Four coordinated dark window themes with optional custom colours and matching selection borders
- Menu backgrounds that match the theme, use a soft gradient colour or use a custom image
- Persistent active-tab highlights, a neutral keyword editing panel, tag-coloured keyword controls and themed footer buttons
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

Curatr code is MIT licensed. See [LICENSE.txt](LICENSE.txt). The selected Font
Awesome icons are licensed under CC BY 4.0; attribution and modification details
are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Development

The source ZIP includes reusable artwork components, editable genre geometry,
pinned Font Awesome SVGs, canonical menu PNGs and release checks. See
[ARTWORK.md](ARTWORK.md) for build and validation commands. These
checks use local Kodi stubs; testing on Android and Xbox remains necessary.
