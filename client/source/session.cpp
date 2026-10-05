#include "session.h"
#include "net.h"
#include "syati.h"

#if defined(SB4E)
#define GAME_ID 0x53423445
#define ADDR_FRAMELOOP_CALL_UPDATE 0x804B6AA0
#else
#error "SMG2 Online: this region has no addresses yet"
#endif

namespace {
    const u32 HELLO_INTERVAL = 60;
    const u32 KEEPALIVE_INTERVAL = 60;
    // With no word from the server for this long, shake hands again
    const u32 SERVER_TIMEOUT = 5 * 60;

    bool sConnected;
    u8 sPlayerId;
    u32 sOnlineCount;
    u32 sFrame;
    u32 sNonce;
    u32 sLastRxFrame;
    u32 sLastHelloFrame;
    u32 sLastTxFrame;

    u8 sIn[Net::MAX_DATAGRAM] __attribute__((aligned(32)));
    u8 sOut[Net::MAX_DATAGRAM] __attribute__((aligned(32)));
    u32 sOutSize;

    void flush() {
        if (sOutSize > 4) {
            Net::send(sOut, sOutSize);
            sLastTxFrame = sFrame;
        }
        sOutSize = 0;
    }

    void disconnect() {
        if (sConnected) {
            OSReport("[SMG2O] lost the server, reconnecting\n");
        }
        sConnected = false;
        SaveSync::onDisconnect();
        Players::onDisconnect();
    }

    void onWelcome(const u8 *pPayload, u32 size) {
        if (size < sizeof(Protocol::WelcomePayload)) {
            return;
        }
        const Protocol::WelcomePayload *pWelcome = (const Protocol::WelcomePayload *)pPayload;
        if (size < sizeof(Protocol::WelcomePayload) + pWelcome->localRangeCount * sizeof(Protocol::LocalRange)) {
            return;
        }
        // The server answers every HELLO; only the first answer starts a session
        if (sConnected) {
            return;
        }

        sConnected = true;
        sPlayerId = pWelcome->playerId;
        OSReport("[SMG2O] connected as player %d (world %08x)\n", sPlayerId, pWelcome->epoch);
        SaveSync::onWelcome(pWelcome, (const Protocol::LocalRange *)(pWelcome + 1));
    }

    void onDatagram(const u8 *pData, u32 size) {
        if (size < 4 || *(const u32 *)pData != Protocol::MAGIC) {
            return;
        }
        sLastRxFrame = sFrame;

        u32 offset = 4;
        while (offset + sizeof(Protocol::RecordHeader) <= size) {
            const Protocol::RecordHeader *pHeader = (const Protocol::RecordHeader *)(pData + offset);
            const u8 *pPayload = pData + offset + sizeof(Protocol::RecordHeader);
            u32 payloadSize = pHeader->size;
            offset += sizeof(Protocol::RecordHeader) + ((payloadSize + 3) & ~3);
            if (offset > size) {
                break;
            }

            switch (pHeader->tag) {
            case Protocol::TAG_WELCOME:
                onWelcome(pPayload, payloadSize);
                break;
            case Protocol::TAG_PLAYER_POSE:
                if (sConnected && payloadSize >= sizeof(Protocol::PosePayload)) {
                    Players::onPose(pHeader->arg, (const Protocol::PosePayload *)pPayload);
                }
                break;
            case Protocol::TAG_SERVER_KEEPALIVE:
                sOnlineCount = pHeader->arg;
                break;
            case Protocol::TAG_SAVE_ACK:
            case Protocol::TAG_UPDATE_BLOCK:
            case Protocol::TAG_UPDATE_COMMIT:
                if (sConnected) {
                    SaveSync::onRecord(pHeader->tag, pPayload, payloadSize);
                }
                break;
            }
        }
    }

    void update() {
        sFrame++;

        if (!Net::isReady()) {
            Net::start();
            return;
        }
        if (sNonce == 0) {
            sNonce = OSGetTick() | 1;
            sLastRxFrame = sFrame;
        }

        u32 size;
        while ((size = Net::receive(sIn)) != 0) {
            onDatagram(sIn, size);
        }

        if (sConnected && sFrame - sLastRxFrame > SERVER_TIMEOUT) {
            disconnect();
        }

        if (!sConnected) {
            if (sFrame - sLastHelloFrame >= HELLO_INTERVAL || sLastHelloFrame == 0) {
                sLastHelloFrame = sFrame;
                Protocol::HelloPayload hello;
                hello.gameId = GAME_ID;
                hello.nonce = sNonce;
                Session::put(Protocol::TAG_HELLO, 0, &hello, sizeof(hello));
            }
        }
        else {
            SaveSync::update();
        }
        Players::update();
        Title::update();
        if (sConnected) {
            if (sOutSize <= 4 && sFrame - sLastTxFrame >= KEEPALIVE_INTERVAL) {
                Session::put(Protocol::TAG_KEEPALIVE, 0, NULL, 0);
            }
        }

        flush();
    }

    void updateGameSystem(GameSystem *pSystem) {
        pSystem->update();
        update();
    }

    kmCall(ADDR_FRAMELOOP_CALL_UPDATE, updateGameSystem);
};

namespace Session {
    bool isConnected() {
        return sConnected;
    }

    u8 getPlayerId() {
        return sPlayerId;
    }

    u32 getOnlineCount() {
        return sOnlineCount;
    }

    u32 getFrame() {
        return sFrame;
    }

    bool put(u8 tag, u8 arg, const void *pHeader, u32 headerSize, const void *pData, u32 dataSize) {
        u32 size = headerSize + dataSize;
        u32 padded = (size + 3) & ~3;
        if (!Net::isReady() || 4 + sizeof(Protocol::RecordHeader) + padded > Net::MAX_DATAGRAM) {
            return false;
        }
        if (sOutSize != 0 && sOutSize + sizeof(Protocol::RecordHeader) + padded > Net::MAX_DATAGRAM) {
            flush();
        }
        if (sOutSize == 0) {
            *(u32 *)sOut = Protocol::MAGIC;
            sOutSize = 4;
        }

        Protocol::RecordHeader *pRecord = (Protocol::RecordHeader *)(sOut + sOutSize);
        pRecord->tag = tag;
        pRecord->arg = arg;
        pRecord->size = (u16)size;
        u8 *pPayload = (u8 *)(pRecord + 1);
        if (headerSize) {
            memcpy(pPayload, pHeader, headerSize);
        }
        if (dataSize) {
            memcpy(pPayload + headerSize, pData, dataSize);
        }
        for (u32 i = size; i < padded; i++) {
            pPayload[i] = 0;
        }
        sOutSize += sizeof(Protocol::RecordHeader) + padded;
        return true;
    }

    bool put(u8 tag, u8 arg, const void *pPayload, u32 size) {
        return put(tag, arg, pPayload, size, NULL, 0);
    }
};
