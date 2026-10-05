#pragma once

#include "protocol.h"

// Connection to the server: handshake, timeouts, and packing records into
// datagrams. Everything here runs on the game thread.
namespace Session {
    bool isConnected();
    u8 getPlayerId();

    // Players on the server, this one included
    u32 getOnlineCount();

    // Frames since boot
    u32 getFrame();

    // Append a record to the datagram going out this frame. A full datagram is
    // sent and a new one started, so this only fails when the network is down.
    bool put(u8 tag, u8 arg, const void *pPayload, u32 size);

    // Same, for a record made of a fixed header followed by data
    bool put(u8 tag, u8 arg, const void *pHeader, u32 headerSize, const void *pData, u32 dataSize);
};

// What the player chose in the mod menu (menu.cpp)
namespace Settings {
    extern bool showPlayers;
    extern bool playerColours;
    extern bool shareProgress;
};

// The title screen (title.cpp)
namespace Title {
    void update();
};

namespace Menu {
    void onSceneInit();
    void onSceneDestroy();
    void update();
};

// Shared save progress (savesync.cpp)
namespace SaveSync {
    void onWelcome(const Protocol::WelcomePayload *pWelcome, const Protocol::LocalRange *pRanges);
    void onDisconnect();
    void onRecord(u8 tag, const u8 *pPayload, u32 size);
    void update();

    // True while a save file is loaded and being played
    void setInGame(bool inGame);

    // "Share progress" was switched in the menu
    void onSettingChanged();
};

// Other players in the stage (players.cpp)
namespace Players {
    void onPose(u8 playerId, const Protocol::PosePayload *pPose);
    void onDisconnect();
    void onColoursChanged();
    void update();
};
