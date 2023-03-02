"""For media players that are controlled via MQTT."""
import hashlib
import logging

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import (
    MediaPlayerState,
    MediaPlayerEntityFeature,
    MediaType,
)

from homeassistant.components.mqtt import publish, async_subscribe
from homeassistant.components.mqtt.const import CONF_TOPIC
from homeassistant.components.mqtt.util import valid_publish_topic
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import voluptuous as vol


from .const import (
    DOMAIN,
    COMMAND_PAUSE,
    COMMAND_PLAY,
    COMMAND_SKIP_NEXT,
    COMMAND_SKIP_PREVIOUS,
    COMMAND_VOLUME_DOWN,
    COMMAND_VOLUME_UP,
    TOP_LEVEL_TOPIC_ARTIST,
    TOP_LEVEL_TOPIC_ALBUM,
    TOP_LEVEL_TOPIC_COVER,
    TOP_LEVEL_TOPIC_PLAY_END,
    TOP_LEVEL_TOPIC_PLAY_START,
    TOP_LEVEL_TOPIC_REMOTE,
    TOP_LEVEL_TOPIC_TITLE,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_TOPIC): valid_publish_topic,
    },
    extra=vol.REMOVE_EXTRA,
)

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_STEP
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
    """Set up the MQTT media players."""
    _LOGGER.debug(config)

    async_add_entities([ShairportSyncMediaPlayer(
        hass,
        config.get(CONF_NAME),
        config.get(CONF_TOPIC),
    )])


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup the media player platform for Shairport Sync."""

    # Get config from entry
    config = config_entry.data

    async_add_entities([
        ShairportSyncMediaPlayer(
            hass,
            config.get(CONF_NAME),
            config.get(CONF_TOPIC),
        )
    ])


class ShairportSyncMediaPlayer(MediaPlayerEntity):
    """Representation of an MQTT-controlled media player."""

    def __init__(self, hass, name, topic) -> None:
        """Initialize the MQTT media device."""
        _LOGGER.debug("Initialising %s", name)
        self.hass = hass
        self._name = name
        self._base_topic = topic
        self._remote_topic = f"{self._base_topic}/{TOP_LEVEL_TOPIC_REMOTE}"
        self._player_state = MediaPlayerState.IDLE
        self._title = None
        self._artist = None
        self._album = None
        self._media_image = None
        self._subscriptions = []

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        await self._subscribe_to_topics()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        _LOGGER.debug("Removing %s subscriptions", len(self._subscriptions))
        for unsubscribe in self._subscriptions:
            unsubscribe()

    async def _subscribe_to_topics(self):
        """(Re)Subscribe to topics."""

        @callback
        def play_started(_) -> None:
            """Handle the play MQTT message."""
            _LOGGER.debug("Play started")
            self._player_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()

        @callback
        def play_ended(_) -> None:
            """Handle the pause MQTT message."""
            _LOGGER.debug("Play ended")
            self._player_state = MediaPlayerState.PAUSED
            self.async_write_ha_state()

        @callback
        def artist_updated(message) -> None:
            """Handle the artist updated MQTT message."""
            self._artist = message.payload
            _LOGGER.debug("New artist: %s", self._artist)
            self.async_write_ha_state()

        @callback
        def album_updated(message) -> None:
            """Handle the album updated MQTT message."""
            self._album = message.payload
            _LOGGER.debug("New album: %s", self._album)
            self.async_write_ha_state()

        @callback
        def title_updated(message) -> None:
            """Handle the title updated MQTT message."""
            self._title = message.payload
            _LOGGER.debug("New title: %s", self._title)
            self.async_write_ha_state()

        @callback
        def artwork_updated(message) -> None:
            """Handle the artwork updated MQTT message."""
            # https://en.wikipedia.org/wiki/Magic_number_%28programming%29
            # https://en.wikipedia.org/wiki/List_of_file_signatures
            header = " ".join("{:02X}".format(b) for b in message.payload[:4])
            _LOGGER.debug(
                "New artwork (%s bytes); header: %s", len(message.payload), header
            )
            self._media_image = message.payload
            self.async_write_ha_state()

        topic_map = {
            TOP_LEVEL_TOPIC_PLAY_START: (play_started, "utf-8"),
            TOP_LEVEL_TOPIC_PLAY_END: (play_ended, "utf-8"),
            TOP_LEVEL_TOPIC_ARTIST: (artist_updated, "utf-8"),
            TOP_LEVEL_TOPIC_ALBUM: (album_updated, "utf-8"),
            TOP_LEVEL_TOPIC_TITLE: (title_updated, "utf-8"),
            TOP_LEVEL_TOPIC_COVER: (artwork_updated, None),
        }

        for (top_level_topic, (topic_callback, encoding)) in topic_map.items():
            topic = f"{self._base_topic}/{top_level_topic}"
            _LOGGER.debug(
                "Subscribing to topic %s with callback %s",
                topic,
                topic_callback.__name__,
            )
            subscription = await async_subscribe(
                self.hass, topic, topic_callback, encoding=encoding
            )
            self._subscriptions.append(subscription)

    @property
    def should_poll(self) -> bool:
        """No polling needed."""
        return False

    @property
    def unique_id(self) -> str:
        return f"shairport-sync-{self._base_topic}"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {
                (DOMAIN, self._base_topic)
            },
            "name": self.name,
            "manufacturer": "mikebrady",
        }

    @property
    def name(self) -> str:
        """Return the name of the player."""
        _LOGGER.debug("Getting name: %s", self._name)
        return self._name

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the current state of the media player."""
        _LOGGER.debug("Getting state: %s", self._player_state)
        return self._player_state

    @property
    def media_content_type(self) -> MediaType | None:
        """Return the content type of currently playing media."""
        _LOGGER.debug("Getting media content type: %s", MediaType.MUSIC)
        return MediaType.MUSIC

    @property
    def media_title(self) -> str | None:
        """Title of current playing media."""
        _LOGGER.debug("Getting media title: %s", self._title)
        return self._title

    @property
    def media_artist(self) -> str | None:
        """Artist of current playing media, music track only."""
        _LOGGER.debug("Getting media artist: %s", self._artist)
        return self._artist

    @property
    def media_album_name(self) -> str | None:
        """Album of current playing media, music track only."""
        _LOGGER.debug("Getting media album: %s", self._album)
        return self._album

    @property
    def media_image_hash(self) -> str | None:
        """Hash value for the media image."""
        if self._media_image:
            image_hash = hashlib.md5(self._media_image).hexdigest()
            _LOGGER.debug("Media image hash: %s", image_hash)
            return image_hash
        return None

    @property
    def supported_features(self) -> int:
        """Flag media player features that are supported."""
        return SUPPORTED_FEATURES

    @property
    def device_class(self) -> MediaPlayerDeviceClass:
        return MediaPlayerDeviceClass.SPEAKER

    # is_on
    # async def async_turn_on(self):  # async?
    #     """Turn the media player on."""
    # async def async_turn_off(self):  # async?
    #     """Turn the media player off."""

    async def async_media_play(self) -> None:
        """Send play command."""
        _LOGGER.debug("Sending play command")
        publish(self.hass, self._remote_topic, COMMAND_PLAY)

    async def async_media_pause(self) -> None:
        """Send pause command."""
        _LOGGER.debug("Sending pause command")
        publish(self.hass, self._remote_topic, COMMAND_PAUSE)

    async def async_media_stop(self) -> None:
        """Send stop command."""
        _LOGGER.debug("Sending stop command")
        publish(self.hass, self._remote_topic, COMMAND_PAUSE)

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        _LOGGER.debug("Sending skip previous command")
        publish(self.hass, self._remote_topic, COMMAND_SKIP_PREVIOUS)

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        _LOGGER.debug("Sending skip next command")
        publish(self.hass, self._remote_topic, COMMAND_SKIP_NEXT)

    async def async_play_media(self, media_type, media_id, **kwargs) -> None:
        """Play a piece of media."""
        _LOGGER.debug("Sending play media command")
        publish(self.hass, self._remote_topic, COMMAND_PLAY)

    async def async_volume_up(self) -> None:
        """Turn volume up for media player."""
        _LOGGER.debug("Sending volume up command")
        publish(self.hass, self._remote_topic, COMMAND_VOLUME_UP)

    async def async_volume_down(self) -> None:
        """Turn volume down for media player."""
        _LOGGER.debug("Sending volume down command")
        publish(self.hass, self._remote_topic, COMMAND_VOLUME_DOWN)

    async def async_media_play_pause(self) -> None:
        """Play or pause the media player."""
        _LOGGER.debug(
            "Sending toggle play/pause command; currently %s", self._player_state
        )
        if self._player_state == MediaPlayerState.PLAYING:
            publish(self.hass, self._remote_topic, COMMAND_PAUSE)
        else:
            publish(self.hass, self._remote_topic, COMMAND_PLAY)

    async def async_get_media_image(self) -> tuple[str | None, str | None]:
        """Fetch the image of the currently playing media."""
        _LOGGER.debug("Getting media image")
        if self._media_image:
            return (self._media_image, "image/jpeg")
        return (None, None)


# todo: reload (https://github.com/custom-components/blueprint/blob/master/custom_components/blueprint/__init__.py)
