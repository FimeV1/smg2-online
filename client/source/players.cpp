#include "session.h"
#include "colours.h"
#include "syati.h"
#include "Game/Animation/XanimePlayer.h"

// Other players in the same stage.
//
// Each one is a plain actor with the player's model, animated by the player's
// own animation table: the same recipe the game uses for Cosmic Clones. Every
// other frame we send where our Mario is and what he is playing, and pose the
// actors from what the others sent.

#if defined(SB4E)
#define ADDR_MARIO_INIT_CALL_SET_ACTOR 0x803B7AB8
#define ADDR_SCENE_DTOR_CALL_STOP_PAD 0x80451430
#endif

namespace MR {
    void stopReplayPad();
};

namespace {
    // How many other players can be drawn at once in one stage. The server
    // takes more; players elsewhere cost nothing here.
    const u32 MAX_REMOTE = Colours::MAX_SLOTS;
    const u8 NO_OWNER = 0xFF;

    const u32 SEND_INTERVAL = 2;
    const u32 IDLE_SEND_INTERVAL = 30;
    // A player disappears after this many frames without news
    const u32 POSE_TIMEOUT = 90;

    const f32 SMOOTHING = 0.4f;       // part of the gap closed each frame
    const f32 SNAP_DISTANCE = 600.0f; // further than this is a warp, not a walk

    class RemotePlayer;

    struct Remote {
        u8 owner;
        bool isNew;         // nothing of this player has been shown yet
        bool hasNewPose;
        bool needsPaint;    // the slot changed hands: recolour the outfit
        u32 lastRxFrame;
        Protocol::PosePayload pose;
        RemotePlayer *pActor;
    };

    Remote sRemotes[MAX_REMOTE];
    bool sSceneAlive;
    u32 sStageHash;
    u8 sScenario;

    // XanimePlayer keeps its frame control at 0x20: rate at 0xC, frame at 0x10
    inline f32 &animRate(XanimePlayer *pPlayer) {
        return *(f32 *)(*(u8 **)((u8 *)pPlayer + 0x20) + 0xC);
    }
    inline f32 &animFrame(XanimePlayer *pPlayer) {
        return *(f32 *)(*(u8 **)((u8 *)pPlayer + 0x20) + 0x10);
    }
    inline const f32 *animWeights(const XanimePlayer *pPlayer) {
        return (const f32 *)((const u8 *)pPlayer + 0x10);
    }

    class RemotePlayer : public LiveActor {
    public:
        RemotePlayer(Remote *pRemote) : LiveActor("RemotePlayer") {
            mRemote = pRemote;
            mAnimKind = Protocol::ANIM_NONE;
            mAnimHash = 0;
            mBckName[0] = '\0';
            mPosition.set(0.0f, 0.0f, 0.0f);
            mQuat.x = mQuat.y = mQuat.z = 0.0f;
            mQuat.w = 1.0f;
        }

        virtual void init(const JMapInfoIter &rIter) {
            initModelManagerWithAnm(MR::isPlayerLuigi() ? "Luigi" : "Mario", "MarioAnime", NULL, true);
            Colours::setupActor(this);
            MR::connectToSceneNpc(this);
            MR::initLightCtrl(this);
            MR::invalidateClipping(this);
            makeActorDead();
        }

        virtual void control() {
            Remote *pRemote = mRemote;
            const Protocol::PosePayload &rPose = pRemote->pose;

            TVec3f target(rPose.position[0], rPose.position[1], rPose.position[2]);
            Quaternion targetQuat;
            targetQuat.x = rPose.rotation[0] * (1.0f / 32767.0f);
            targetQuat.y = rPose.rotation[1] * (1.0f / 32767.0f);
            targetQuat.z = rPose.rotation[2] * (1.0f / 32767.0f);
            targetQuat.w = rPose.rotation[3] * (1.0f / 32767.0f);

            TVec3f gap(target);
            gap.sub(mPosition);
            if (pRemote->isNew || PSVECMag((Vec *)&gap) > SNAP_DISTANCE) {
                mPosition.set(target);
                mQuat = targetQuat;
            }
            else {
                gap.scale(SMOOTHING);
                mPosition.add(gap);
                C_QUATSlerp(&mQuat, &targetQuat, &mQuat, SMOOTHING);
            }
            mTranslation.set(mPosition);

            XanimePlayer *pXanime = mModelManager->mXanimePlayer;
            if (pXanime && pRemote->hasNewPose) {
                applyAnimation(pXanime, rPose, pRemote->isNew);
            }
            pRemote->hasNewPose = false;
            pRemote->isNew = false;

            if (rPose.flags & Protocol::POSE_HIDDEN) {
                MR::hideModel(this);
            }
            else {
                MR::showModel(this);
            }
        }

        virtual void calcAnim() {
            // Our outfit textures are current only while our display list is built
            u32 slot = mRemote - sRemotes;
            Colours::begin(slot, this);
            LiveActor::calcAnim();
            Colours::end(this);
        }

        virtual void calcAndSetBaseMtx() {
            Mtx mtx;
            PSMTXQuat(mtx, &mQuat);
            mtx[0][3] = mPosition.x;
            mtx[1][3] = mPosition.y;
            mtx[2][3] = mPosition.z;
            MR::setBaseTRMtx(this, mtx);
        }

    private:
        void applyAnimation(XanimePlayer *pXanime, const Protocol::PosePayload &rPose, bool force) {
            if (rPose.animKind == Protocol::ANIM_GROUP) {
                if (force || mAnimKind != Protocol::ANIM_GROUP || mAnimHash != rPose.animHash) {
                    pXanime->changeAnimationByHash(rPose.animHash);
                }
            }
            else if (rPose.animKind == Protocol::ANIM_BCK) {
                if (force || mAnimKind != Protocol::ANIM_BCK || strncmp(mBckName, rPose.bckName, sizeof(mBckName)) != 0) {
                    memcpy(mBckName, rPose.bckName, sizeof(mBckName));
                    mBckName[sizeof(mBckName) - 1] = '\0';
                    pXanime->changeAnimationBck(mBckName);
                }
            }
            else {
                return;
            }
            mAnimKind = rPose.animKind;
            mAnimHash = rPose.animHash;

            // The sender's frame is the truth; the rate carries it between packets
            animFrame(pXanime) = rPose.animFrame;
            animRate(pXanime) = rPose.animRate;
            for (s32 i = 0; i < 4; i++) {
                pXanime->changeTrackWeight(i, rPose.trackWeights[i] * (1.0f / 255.0f));
            }
        }

        Remote *mRemote;
        TVec3f mPosition;
        Quaternion mQuat;
        u8 mAnimKind;
        u32 mAnimHash;
        char mBckName[24];
    };

    void release(Remote &rRemote) {
        rRemote.owner = NO_OWNER;
        rRemote.hasNewPose = false;
    }

    s16 packUnit(f32 value) {
        if (value >= 1.0f) {
            return 32767;
        }
        if (value <= -1.0f) {
            return -32767;
        }
        return (s16)(value * 32767.0f);
    }

    u8 packWeight(f32 value) {
        if (value <= 0.0f) {
            return 0;
        }
        if (value >= 1.0f) {
            return 255;
        }
        return (u8)(value * 255.0f + 0.5f);
    }

    void sendLocalPose() {
        Protocol::PosePayload pose;
        memset(&pose, 0, sizeof(pose));

        MarioActor *pMario = sSceneAlive ? MarioAccess::getPlayerActor() : NULL;
        if (pMario && pMario->mModelManager) {
            pose.stageHash = sStageHash;
            pose.scenario = sScenario;
            pose.character = MR::isPlayerLuigi() ? 1 : 0;
            if (MR::isPlayerHidden()) {
                pose.flags |= Protocol::POSE_HIDDEN;
            }

            MtxPtr pMtx = pMario->getBaseMtx();
            pose.position[0] = pMtx[0][3];
            pose.position[1] = pMtx[1][3];
            pose.position[2] = pMtx[2][3];

            Quaternion quat;
            C_QUATMtx(&quat, pMtx);
            pose.rotation[0] = packUnit(quat.x);
            pose.rotation[1] = packUnit(quat.y);
            pose.rotation[2] = packUnit(quat.z);
            pose.rotation[3] = packUnit(quat.w);

            XanimePlayer *pXanime = pMario->mModelManager->mXanimePlayer;
            if (pXanime) {
                if (pXanime->isAnimationRunSimple()) {
                    const char *pName = pXanime->getCurrentBckName();
                    if (pName) {
                        pose.animKind = Protocol::ANIM_BCK;
                        strncpy(pose.bckName, pName, sizeof(pose.bckName) - 1);
                    }
                }
                else {
                    const char *pName = pXanime->getCurrentAnimationName();
                    if (pName) {
                        pose.animKind = Protocol::ANIM_GROUP;
                        pose.animHash = MR::getHashCode(pName);
                    }
                }
                pose.animFrame = animFrame(pXanime);
                pose.animRate = animRate(pXanime);
                const f32 *pWeights = animWeights(pXanime);
                for (u32 i = 0; i < 4; i++) {
                    pose.trackWeights[i] = packWeight(pWeights[i]);
                }
            }
        }

        Session::put(Protocol::TAG_POSE, 0, &pose, sizeof(pose));
    }

    // ---- scene lifetime ----

    void onPlayerInit(MarioHolder *pHolder, MarioActor *pActor) {
        pHolder->setMarioActor(pActor);

        for (u32 i = 0; i < MAX_REMOTE; i++) {
            release(sRemotes[i]);
            sRemotes[i].pActor = NULL;
        }

        if (MR::isStageFileSelect()) {
            // Title screen and file select: no save is in play yet
            sStageHash = 0;
            SaveSync::setInGame(false);
            sSceneAlive = true;
            return;
        }

        Colours::reset();
        Menu::onSceneInit();
        sStageHash = Protocol::hashString(MR::getCurrentStageName());
        sScenario = (u8)MR::getCurrentScenarioNo();
        for (u32 i = 0; i < MAX_REMOTE; i++) {
            sRemotes[i].pActor = new RemotePlayer(&sRemotes[i]);
            sRemotes[i].pActor->initWithoutIter();
        }
        SaveSync::setInGame(true);
        sSceneAlive = true;
    }

    kmCall(ADDR_MARIO_INIT_CALL_SET_ACTOR, onPlayerInit);

    void onSceneDestroy() {
        MR::stopReplayPad();

        // Everything the scene allocated is about to be freed
        Menu::onSceneDestroy();
        sSceneAlive = false;
        sStageHash = 0;
        for (u32 i = 0; i < MAX_REMOTE; i++) {
            release(sRemotes[i]);
            sRemotes[i].pActor = NULL;
        }
    }

    kmCall(ADDR_SCENE_DTOR_CALL_STOP_PAD, onSceneDestroy);
};

namespace Players {
    void onPose(u8 playerId, const Protocol::PosePayload *pPose) {
        Remote *pSlot = NULL;
        for (u32 i = 0; i < MAX_REMOTE; i++) {
            if (sRemotes[i].owner == playerId) {
                pSlot = &sRemotes[i];
                break;
            }
        }

        bool isHere = sSceneAlive && sStageHash != 0 && pPose->stageHash == sStageHash && pPose->scenario == sScenario;
        if (!isHere) {
            if (pSlot) {
                release(*pSlot);
            }
            return;
        }

        if (!pSlot) {
            for (u32 i = 0; i < MAX_REMOTE; i++) {
                if (sRemotes[i].owner == NO_OWNER) {
                    pSlot = &sRemotes[i];
                    break;
                }
            }
            if (!pSlot) {
                return;
            }
            pSlot->owner = playerId;
            pSlot->isNew = true;
            pSlot->needsPaint = true;
        }

        pSlot->pose = *pPose;
        pSlot->hasNewPose = true;
        pSlot->lastRxFrame = Session::getFrame();
    }

    void onDisconnect() {
        for (u32 i = 0; i < MAX_REMOTE; i++) {
            release(sRemotes[i]);
        }
    }

    void onColoursChanged() {
        for (u32 i = 0; i < MAX_REMOTE; i++) {
            sRemotes[i].needsPaint = true;
        }
    }

    void update() {
        u32 frame = Session::getFrame();

        if (Session::isConnected()) {
            bool inStage = sSceneAlive && sStageHash != 0;
            if (frame % (inStage ? SEND_INTERVAL : IDLE_SEND_INTERVAL) == 0) {
                sendLocalPose();
            }
        }

        if (!sSceneAlive) {
            return;
        }
        Menu::update();

        for (u32 i = 0; i < MAX_REMOTE; i++) {
            Remote &rRemote = sRemotes[i];
            if (rRemote.owner != NO_OWNER && frame - rRemote.lastRxFrame > POSE_TIMEOUT) {
                release(rRemote);
            }
            if (!rRemote.pActor) {
                continue;
            }

            bool isShown = !MR::isDead(rRemote.pActor);
            bool isWanted = rRemote.owner != NO_OWNER && Settings::showPlayers;
            if (isWanted && rRemote.needsPaint) {
                rRemote.needsPaint = false;
                Colours::paint(i, rRemote.owner, rRemote.pActor);
            }
            if (isWanted && !isShown) {
                rRemote.isNew = true; // do not glide in from where it was last shown
                rRemote.pActor->appear();
            }
            else if (!isWanted && isShown) {
                rRemote.pActor->kill();
            }
        }
    }
};
