#include "session.h"
#include "syati.h"

// Shared save progress.
//
// The game can serialize a whole save file (stars, comet medals, flags, world
// map, bank, ...) into one blob and load it back; that is how it writes to the
// Wii's memory. We use the same two calls to keep every player's file in step:
//
//   report: when our blob changes, send the blocks that changed. The server
//           works out which fields we changed and merges them into the world.
//   update: when the world differs from what we hold, the server sends the
//           blocks that differ and we load the result into the game.
//
// The server knows the layout of the blob; this side only moves bytes. The
// one thing it is told is which byte ranges stay private to each player.
//
// `sAcked` is the last blob both sides agree we hold, named by `sAckedId`.
// Reports are diffs against it, and an update only applies on top of it.

class BinaryDataChunkHolder;

extern "C" {
    u32 makeFileBinary__21BinaryDataChunkHolderFPUcUl(BinaryDataChunkHolder *, u8 *, u32);
    bool loadFromFileBinary__21BinaryDataChunkHolderFPCUcUl(BinaryDataChunkHolder *, const u8 *, u32);
}

namespace {
    const u32 BLOB_CAPACITY = 0x1000;
    const u32 BLOCK = Protocol::SAVE_BLOCK_SIZE;
    const u32 MAX_LOCAL_RANGES = 16;
    const u32 UPDATE_ID_FLAG = 0x80000000;

    const u32 CHECK_INTERVAL = 10;   // frames between looks at the save
    const u32 RESEND_INTERVAL = 45;  // frames before an unanswered report is sent again

    bool sEnabled;  // connected to a server that shares progress
    bool sInGame;

    Protocol::LocalRange sLocalRanges[MAX_LOCAL_RANGES];
    u32 sLocalRangeCount;

    u8 sRaw[BLOB_CAPACITY];     // the game's blob as it is
    u8 sCurrent[BLOB_CAPACITY]; // ... with the private ranges zeroed
    u32 sCurrentSize;

    u8 sAcked[BLOB_CAPACITY];
    u32 sAckedSize;
    u32 sAckedId;
    bool sHasAcked;

    u8 sReport[BLOB_CAPACITY];
    u32 sReportSize;
    u32 sReportSeq;
    bool sReportActive;
    bool sReportFull; // send every block, not only the changed ones
    u32 sReportSentFrame;

    u8 sStaging[BLOB_CAPACITY];
    u32 sStagingSeq;
    u32 sStagingBaseId;
    bool sHasStaging;
    u32 sAppliedSeq;

    BinaryDataChunkHolder *getChunkHolder() {
        GameSystem *pSystem = SingletonHolder<GameSystem>::sInstance;
        if (!pSystem || !pSystem->mSequenceDirector) {
            return NULL;
        }
        // GameSequenceDirector -> SaveDataHandleSequence -> current UserFile
        u8 *pSaveSequence = *(u8 **)((u8 *)pSystem->mSequenceDirector + 0x8);
        if (!pSaveSequence) {
            return NULL;
        }
        UserFile *pFile = *(UserFile **)(pSaveSequence + 0xC);
        if (!pFile || !pFile->mGameDataHolder) {
            return NULL;
        }
        return pFile->mGameDataHolder->mBinaryDataChunkHolder;
    }

    // Fills sRaw and sCurrent. False if there is no save to read.
    bool readSave() {
        BinaryDataChunkHolder *pHolder = getChunkHolder();
        if (!pHolder) {
            return false;
        }
        u32 size = makeFileBinary__21BinaryDataChunkHolderFPUcUl(pHolder, sRaw, BLOB_CAPACITY);
        if (size < 4 || size > BLOB_CAPACITY) {
            return false;
        }

        memcpy(sCurrent, sRaw, size);
        for (u32 i = 0; i < sLocalRangeCount; i++) {
            u32 offset = sLocalRanges[i].offset;
            u32 end = offset + sLocalRanges[i].size;
            for (; offset < end && offset < size; offset++) {
                sCurrent[offset] = 0;
            }
        }
        sCurrentSize = size;
        return true;
    }

    bool isSame(const u8 *pA, u32 sizeA, const u8 *pB, u32 sizeB) {
        return sizeA == sizeB && memcmp(pA, pB, sizeA) == 0;
    }

    void sendReport() {
        sReportSentFrame = Session::getFrame();
        bool full = sReportFull || !sHasAcked || sAckedSize != sReportSize;

        for (u32 offset = 0; offset < sReportSize; offset += BLOCK) {
            u32 size = sReportSize - offset < BLOCK ? sReportSize - offset : BLOCK;
            if (!full && memcmp(sReport + offset, sAcked + offset, size) == 0) {
                continue;
            }
            Protocol::SaveBlockHeader header;
            header.seq = sReportSeq;
            header.offset = (u16)offset;
            header.size = (u16)size;
            Session::put(Protocol::TAG_SAVE_BLOCK, 0, &header, sizeof(header), sReport + offset, size);
        }

        Protocol::SaveCommitPayload commit;
        commit.seq = sReportSeq;
        commit.baseSeq = sHasAcked ? sAckedId : 0;
        commit.checksum = Protocol::checksum(sReport, sReportSize);
        commit.totalSize = (u16)sReportSize;
        commit.flags = sHasAcked ? 0 : Protocol::SAVE_FLAG_INITIAL;
        Session::put(Protocol::TAG_SAVE_COMMIT, 0, &commit, sizeof(commit));
    }

    void startReport() {
        memcpy(sReport, sCurrent, sCurrentSize);
        sReportSize = sCurrentSize;
        sReportSeq = (sReportSeq + 1) & ~UPDATE_ID_FLAG;
        if (sReportSeq == 0) {
            sReportSeq = 1;
        }
        sReportActive = true;
        sReportFull = false;
        sendReport();
    }

    void onAck(const Protocol::SaveAckPayload *pAck) {
        if (!sReportActive || pAck->seq != sReportSeq) {
            return;
        }
        if (!pAck->ok) {
            sReportFull = true;
            sendReport();
            return;
        }
        memcpy(sAcked, sReport, sReportSize);
        sAckedSize = sReportSize;
        sAckedId = sReportSeq;
        sHasAcked = true;
        sReportActive = false;
    }

    void onUpdateBlock(const Protocol::SaveBlockHeader *pHeader, u32 size) {
        if (!sHasAcked || size < sizeof(*pHeader) + pHeader->size) {
            return;
        }
        if (pHeader->offset + pHeader->size > BLOB_CAPACITY) {
            return;
        }
        if (!sHasStaging || sStagingSeq != pHeader->seq || sStagingBaseId != sAckedId) {
            memcpy(sStaging, sAcked, sAckedSize);
            sStagingSeq = pHeader->seq;
            sStagingBaseId = sAckedId;
            sHasStaging = true;
        }
        memcpy(sStaging + pHeader->offset, pHeader + 1, pHeader->size);
    }

    void onUpdateCommit(const Protocol::SaveCommitPayload *pCommit) {
        if (pCommit->seq == sAppliedSeq && sAppliedSeq != 0) {
            // Our answer got lost
            Session::put(Protocol::TAG_SAVE_APPLIED, 0, &sAppliedSeq, sizeof(sAppliedSeq));
            return;
        }
        if (!sInGame || !sHasAcked || sReportActive || pCommit->baseSeq != sAckedId) {
            return;
        }
        if (!sHasStaging || sStagingSeq != pCommit->seq || sStagingBaseId != sAckedId) {
            return;
        }
        u32 size = pCommit->totalSize;
        if (size != sAckedSize || Protocol::checksum(sStaging, size) != pCommit->checksum) {
            return;
        }

        // Something may have changed here since the last look. Then our
        // report goes first; the server folds it in and sends a new update.
        if (!readSave() || !isSame(sCurrent, sCurrentSize, sAcked, sAckedSize)) {
            return;
        }

        BinaryDataChunkHolder *pHolder = getChunkHolder();
        if (!pHolder) {
            return;
        }

        // The private ranges keep what this game has
        memcpy(sCurrent, sStaging, size);
        for (u32 i = 0; i < sLocalRangeCount; i++) {
            u32 offset = sLocalRanges[i].offset;
            u32 end = offset + sLocalRanges[i].size;
            for (; offset < end && offset < size; offset++) {
                sCurrent[offset] = sRaw[offset];
            }
        }
        loadFromFileBinary__21BinaryDataChunkHolderFPCUcUl(pHolder, sCurrent, size);

        memcpy(sAcked, sStaging, size);
        sAckedId = pCommit->seq | UPDATE_ID_FLAG;
        sAppliedSeq = pCommit->seq;
        sHasStaging = false;
        Session::put(Protocol::TAG_SAVE_APPLIED, 0, &sAppliedSeq, sizeof(sAppliedSeq));
        OSReport("[SMG2O] save progress updated (%d)\n", sAppliedSeq);
    }

    void reset() {
        sHasAcked = false;
        sReportActive = false;
        sHasStaging = false;
        sAppliedSeq = 0;
    }
};

namespace SaveSync {
    void onWelcome(const Protocol::WelcomePayload *pWelcome, const Protocol::LocalRange *pRanges) {
        reset();
        sEnabled = pWelcome->shareSave != 0;
        sLocalRangeCount = pWelcome->localRangeCount < MAX_LOCAL_RANGES ? pWelcome->localRangeCount : MAX_LOCAL_RANGES;
        for (u32 i = 0; i < sLocalRangeCount; i++) {
            sLocalRanges[i] = pRanges[i];
        }
    }

    void onDisconnect() {
        sEnabled = false;
        reset();
    }

    void setInGame(bool inGame) {
        if (inGame != sInGame) {
            // Another file may be loaded next: start over from a full report
            reset();
        }
        sInGame = inGame;
    }

    void onRecord(u8 tag, const u8 *pPayload, u32 size) {
        if (!sEnabled || !Settings::shareProgress) {
            return;
        }
        switch (tag) {
        case Protocol::TAG_SAVE_ACK:
            if (size >= sizeof(Protocol::SaveAckPayload)) {
                onAck((const Protocol::SaveAckPayload *)pPayload);
            }
            break;
        case Protocol::TAG_UPDATE_BLOCK:
            if (size >= sizeof(Protocol::SaveBlockHeader)) {
                onUpdateBlock((const Protocol::SaveBlockHeader *)pPayload, size);
            }
            break;
        case Protocol::TAG_UPDATE_COMMIT:
            if (size >= sizeof(Protocol::SaveCommitPayload)) {
                onUpdateCommit((const Protocol::SaveCommitPayload *)pPayload);
            }
            break;
        }
    }

    void onSettingChanged() {
        // Back on: start from a full report, as after joining
        reset();
    }

    void update() {
        if (!sEnabled || !sInGame || !Settings::shareProgress) {
            return;
        }

        u32 frame = Session::getFrame();
        if (frame % CHECK_INTERVAL == 0 && readSave()) {
            if (!sReportActive) {
                if (!sHasAcked || !isSame(sCurrent, sCurrentSize, sAcked, sAckedSize)) {
                    startReport();
                }
            }
            else if (!isSame(sCurrent, sCurrentSize, sReport, sReportSize)) {
                // Changed again before the server answered
                startReport();
            }
        }

        if (sReportActive && frame - sReportSentFrame >= RESEND_INTERVAL) {
            sendReport();
        }
    }
};
