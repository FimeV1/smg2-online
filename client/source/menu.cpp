#include "session.h"
#include "syati.h"
#include "Game/Screen/InformationMessage.h"

// The mod menu: press - (Minus) in a stage.
//
//   1    next line
//   2    change it
//   -    close
//
// These are buttons the game itself does not use, so nothing happens to
// Mario or the camera while the menu is up; the game keeps running underneath.
// It is drawn with the game's own information window, in a private copy so
// the game's messages are not disturbed.

namespace Settings {
    bool showPlayers = true;
    bool playerColours = true;
    bool shareProgress = true;
};

namespace {
    const s32 PAD = 0;
    const u32 REFRESH_INTERVAL = 30; // the status line follows the connection

    enum Item {
        ITEM_PLAYERS,
        ITEM_COLOURS,
        ITEM_PROGRESS,
        ITEM_COUNT
    };

    InformationMessage *sWindow;
    bool sOpen;
    s32 sCursor;
    wchar_t sText[256];
    u32 sLength;

    void put(const char *pText) {
        while (*pText && sLength < sizeof(sText) / sizeof(sText[0]) - 1) {
            sText[sLength++] = (wchar_t)(u8)*pText++;
        }
        sText[sLength] = 0;
    }

    void putNumber(u32 value) {
        char digits[12];
        s32 count = 0;
        do {
            digits[count++] = (char)('0' + value % 10);
            value /= 10;
        } while (value && count < 11);
        while (count > 0 && sLength < sizeof(sText) / sizeof(sText[0]) - 1) {
            sText[sLength++] = (wchar_t)digits[--count];
        }
        sText[sLength] = 0;
    }

    void putItem(s32 item, const char *pLabel, const char *pValue) {
        put(item == sCursor ? "\n> " : "\n   ");
        put(pLabel);
        put(pValue);
    }

    void refresh() {
        sLength = 0;
        if (Session::isConnected()) {
            put("SMG2 Online: player ");
            putNumber(Session::getPlayerId() + 1);
            put(" of ");
            putNumber(Session::getOnlineCount());
        }
        else {
            put("SMG2 Online: no server yet");
        }
        putItem(ITEM_PLAYERS, "Other players: ", Settings::showPlayers ? "shown" : "hidden");
        putItem(ITEM_COLOURS, "Player colours: ", Settings::playerColours ? "on" : "off");
        putItem(ITEM_PROGRESS, "Share progress: ", Settings::shareProgress ? "on" : "off");
        put("\n(1) next   (2) change   (-) close");
        sWindow->setMessage(sText);
    }

    void change() {
        switch (sCursor) {
        case ITEM_PLAYERS:
            Settings::showPlayers = !Settings::showPlayers;
            break;
        case ITEM_COLOURS:
            Settings::playerColours = !Settings::playerColours;
            Players::onColoursChanged();
            break;
        case ITEM_PROGRESS:
            Settings::shareProgress = !Settings::shareProgress;
            SaveSync::onSettingChanged();
            break;
        }
        MR::startSystemSE("SE_SY_CURSOR_1", -1, -1);
    }

    void open() {
        sOpen = true;
        refresh();
        sWindow->mWindowType = InformationMessage::InformationMessage_WindowCenter;
        sWindow->appear();
        sWindow->mCanClose = false; // or the next press of A (a jump) would close it
    }

    void close() {
        sOpen = false;
        sWindow->disappear();
    }
};

namespace Menu {
    void onSceneInit() {
        sOpen = false;
        sWindow = new InformationMessage(true);
        sWindow->initWithoutIter();
    }

    void onSceneDestroy() {
        sOpen = false;
        sWindow = NULL;
    }

    void update() {
        if (!sWindow) {
            return;
        }

        if (MR::testCorePadTriggerMinus(PAD)) {
            if (sOpen) {
                close();
            }
            else {
                open();
            }
            return;
        }
        if (!sOpen) {
            return;
        }

        bool changed = false;
        if (MR::testCorePadTrigger1(PAD)) {
            sCursor = (sCursor + 1) % ITEM_COUNT;
            MR::startSystemSE("SE_SY_CURSOR_1", -1, -1);
            changed = true;
        }
        else if (MR::testCorePadTrigger2(PAD)) {
            change();
            changed = true;
        }

        if (changed || Session::getFrame() % REFRESH_INTERVAL == 0) {
            refresh();
        }
    }
};
