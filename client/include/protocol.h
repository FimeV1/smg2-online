#pragma once

#include "revolution.h"

// Wire format shared with server/smg2_server.py. Everything is big endian.
//
// A datagram is `u32 MAGIC` followed by records. A record is
// `u8 tag, u8 arg, u16 size` + `size` payload bytes, padded to a multiple of 4.
namespace Protocol {
    const u32 MAGIC = 0x53324F01; // "S2O" + protocol version

    enum Tag {
        // client -> server
        TAG_HELLO = 1,        // HelloPayload
        TAG_POSE = 2,         // PosePayload
        TAG_SAVE_BLOCK = 3,   // SaveBlockHeader + data: part of report `seq`
        TAG_SAVE_COMMIT = 4,  // SaveCommitPayload: report `seq` is complete
        TAG_SAVE_APPLIED = 5, // u32 seq: update `seq` is now this game's save
        TAG_KEEPALIVE = 6,

        // server -> client
        TAG_WELCOME = 16,     // WelcomePayload + local ranges
        TAG_PLAYER_POSE = 17, // u8 id in `arg`, PosePayload
        TAG_SAVE_ACK = 18,    // SaveAckPayload: report `seq` was merged (or must be resent in full)
        TAG_UPDATE_BLOCK = 19, // SaveBlockHeader + data: part of update `seq`
        TAG_UPDATE_COMMIT = 20, // SaveCommitPayload: update `seq` is complete
        TAG_SERVER_KEEPALIVE = 21 // every second; `arg` = players online
    };

    struct RecordHeader {
        u8 tag;
        u8 arg;
        u16 size;
    };

    struct HelloPayload {
        u32 gameId;   // 'SB4E'
        u32 nonce;    // random per boot, so the server can tell a restart from a resend
    };

    // A range of the save blob that stays private to each player (lives etc.)
    struct LocalRange {
        u16 offset;
        u16 size;
    };

    struct WelcomePayload {
        u32 epoch;       // identifies the server's world; changes when it is reset
        u8 playerId;
        u8 maxPlayers;
        u8 shareSave;    // 0 = save sync is off on this server
        u8 localRangeCount;
        // LocalRange[localRangeCount] follows
    };

    enum AnimKind {
        ANIM_NONE = 0,
        ANIM_GROUP = 1, // animHash names an Xanime group
        ANIM_BCK = 2    // bckName names a single BCK
    };

    enum PoseFlags {
        POSE_HIDDEN = 1 // in the stage but not drawn (in a pipe, a cannon, ...)
    };

    struct PosePayload {
        u32 stageHash;   // 0 = not in a stage
        u8 scenario;
        u8 character;    // 0 Mario, 1 Luigi
        u8 flags;
        u8 animKind;
        f32 position[3];
        s16 rotation[4]; // quaternion x y z w, 1.0 = 0x7FFF
        f32 animFrame;
        f32 animRate;
        u8 trackWeights[4]; // 255 = 1.0
        u32 animHash;
        char bckName[24];
    };

    const u32 SAVE_BLOCK_SIZE = 256;
    const u32 SAVE_FLAG_INITIAL = 1; // first report of a session: the server has no base for it

    struct SaveBlockHeader {
        u32 seq;
        u16 offset;
        u16 size;
    };

    struct SaveCommitPayload {
        u32 seq;
        u32 baseSeq;  // report: unused. update: the report this update builds on
        u32 checksum; // of the whole blob after applying the blocks
        u16 totalSize;
        u16 flags;
    };

    struct SaveAckPayload {
        u32 seq;
        u32 ok; // 0 = blocks were missing: send every block again
    };

    // FNV-1a
    inline u32 checksum(const u8 *pData, u32 size) {
        u32 hash = 0x811C9DC5;
        for (u32 i = 0; i < size; i++) {
            hash = (hash ^ pData[i]) * 0x01000193;
        }
        return hash;
    }

    inline u32 hashString(const char *pText) {
        u32 hash = 0x811C9DC5;
        for (; *pText; pText++) {
            hash = (hash ^ (u8)*pText) * 0x01000193;
        }
        return hash ? hash : 1;
    }
};
