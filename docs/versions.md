# Which version is taken

In order, and the first one that exists wins:

1. Original audio with Spanish subtitles (`Subtitulos en español`).
2. Original audio with English subtitles (`English Subtitles`).

A dubbed track is never taken as a fallback. Some arcs are offered on the Spanish page only as `Doblaje en español`, and treating that as "the Spanish version" is how a Castilian-audio Water Seven ended up in the library: an arc like that skips the Spanish page entirely and takes English subtitles over the same original audio.

`--audio dub` reverses the preference, and is just as strict: it takes a dub or nothing.

## The Spanish dub as a second version

A dub is never a fallback, but it is worth having beside the subtitled files rather than instead of them. Plex reads two files of the same episode as two versions of it and offers both under *Play Version*, so a season plays either with Spanish audio or with the original audio and subtitles.

It is downloaded for the episodes that have one, which is usually fewer than the arc holds: One Pace dubs an arc long after it subtitles it, so Water Seven is 20 subtitled episodes and 5 dubbed ones.

A dub folder is padded out to the length of the arc with the subtitled release, the same pixeldrain file under the same name, so its file count says nothing about how much is dubbed: Baratie offers eight files there and only the first is dubbed. Only the files whose name says `[Es Dub]` are taken. The rest would put the season's own episodes in a second time.

What should play is the original audio with Spanish subtitles, and Plex orders the versions of an episode by resolution and plays the first. So in a season taken with Spanish subtitles the dub is held to that resolution or lower, never higher: at equal resolution Plex keeps the file that was already there, which is the subtitled one, and an arc publishing its dub only above it is skipped. A season taken with English subtitles has nothing to protect, so there the dub is taken at `--resolution` like anything else and may well be what a click on play gives you.

Neither version costs the server any transcoding: both are h264 with AAC stereo, and the subtitles are burnt into the subtitled release, so there is nothing to convert or to burn in on the fly. It is a version change and not an audio-track change, because the two files are separate encodes, and the dub carries no subtitles at all.

`--no-dub-version` turns this off. Dub files already on disk are left where they are.

## Which version is which

Plex builds the label in its version picker out of bitrate and resolution alone, and there is no field anywhere to name a version: editions are a movie-library feature and a series library ignores the file, and the API rejects a title on a media item. So two versions of the same episode at the same resolution read identically, `2.1 Mbps, 1080p` twice, and 31 of the episodes with a dub are in that position.

What tells them apart is the audio language, which Plex names on the episode once a version is selected and in the player's audio menu. One Pace sets it on some releases and leaves it unset on others, and Plex shows `Unknown` for those, so after each arc every file is made to declare the language its name implies: `[Es Sub]` and `[En Sub]` carry the original audio and get `jpn`, `[Es Dub]` gets `spa`.

It is two bytes in the `mdhd` box of the audio track, written in place: nothing is re-encoded, the container is untouched, and no track is added or dropped. Writing it does not change the file's size, so Plex is asked to re-analyze the season afterwards; a scan alone would keep serving the streams it already read.

`--no-audio-language` leaves the files alone.
