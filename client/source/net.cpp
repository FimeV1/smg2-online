#include "net.h"
#include "syati.h"

// IOS (not declared by Syati)
struct IOSIoVector {
    void *base;
    u32 length;
};

extern "C" {
    s32 IOS_Open(const char *pPath, u32 mode);
    s32 IOS_Close(s32 fd);
    s32 IOS_Ioctl(s32 fd, s32 cmd, void *pIn, u32 inSize, void *pOut, u32 outSize);
    s32 IOS_Ioctlv(s32 fd, s32 cmd, u32 inCount, u32 outCount, IOSIoVector *pVectors);
}

#define ALIGN32 __attribute__((aligned(32)))

namespace {
    const s32 IOCTL_NWC24_STARTUP_SOCKET = 0x06;
    const s32 IOCTL_SO_RECVFROM = 0x0C;
    const s32 IOCTL_SO_SENDTO = 0x0D;
    const s32 IOCTL_SO_SOCKET = 0x0F;
    const s32 IOCTL_SO_GETHOSTID = 0x10;
    const s32 IOCTL_SO_STARTUP = 0x1F;

    const char *SERVER_ADDRESS_FILE = "/CustomCode/serverIP.txt";
    const u16 DEFAULT_PORT = 5030;

    struct SockAddr {
        u8 len;
        u8 family;
        u16 port;
        u32 addr;
    };

    struct SendToParams {
        s32 fd;
        u32 flags;
        u32 hasAddr;
        u8 addr[28];
    };

    enum State {
        STATE_IDLE,
        STATE_STARTING,
        STATE_READY,
        STATE_FAILED
    };

    volatile s32 sState = STATE_IDLE;
    s32 sTopFd = -1;
    s32 sSocket = -1;
    SockAddr sServer = { 8, 2, DEFAULT_PORT, 0x7F000001 };
    volatile bool sHasSent = false;

    // ---- queues (guarded by disabling interrupts; copies are tiny) ----
    const u32 RX_SLOTS = 12;
    const u32 TX_SLOTS = 4;

    u8 sRxData[RX_SLOTS][Net::MAX_DATAGRAM];
    u16 sRxSize[RX_SLOTS];
    u32 sRxHead, sRxCount;

    u8 sTxData[TX_SLOTS][Net::MAX_DATAGRAM];
    u16 sTxSize[TX_SLOTS];
    u32 sTxHead, sTxCount;
    OSThreadQueue sTxWait;

    // ---- threads ----
    const u32 STACK_SIZE = 0x2000;
    u8 sRxStack[STACK_SIZE] ALIGN32;
    u8 sTxStack[STACK_SIZE] ALIGN32;
    OSThread sRxThread;
    OSThread sTxThread;

    // Buffers handed to IOS must be 32-byte aligned and must not be on a
    // stack that could move; each is used by exactly one thread.
    u8 sRxBuffer[Net::MAX_DATAGRAM] ALIGN32;
    u32 sRxParams[8] ALIGN32;
    IOSIoVector sRxVectors[3] ALIGN32;

    u8 sTxBuffer[Net::MAX_DATAGRAM] ALIGN32;
    SendToParams sTxParams ALIGN32;
    IOSIoVector sTxVectors[2] ALIGN32;

    u8 sInitBuffer[64] ALIGN32;

    void sleepMs(u32 ms) {
        OSSleepTicks(OSMillisecondsToTicks((OSTime)ms));
    }

    bool isDigit(char c) {
        return c >= '0' && c <= '9';
    }

    // "a.b.c.d" or "a.b.c.d:port", surrounded by anything
    bool parseAddress(const char *pText, u32 size, SockAddr *pOut) {
        u32 i = 0;
        while (i < size && !isDigit(pText[i])) {
            i++;
        }

        u32 addr = 0;
        for (u32 octet = 0; octet < 4; octet++) {
            u32 value = 0;
            u32 digits = 0;
            while (i < size && isDigit(pText[i]) && digits < 3) {
                value = value * 10 + (pText[i] - '0');
                i++;
                digits++;
            }
            if (digits == 0 || value > 255) {
                return false;
            }
            if (octet < 3) {
                if (i >= size || pText[i] != '.') {
                    return false;
                }
                i++;
            }
            addr = (addr << 8) | value;
        }

        u32 port = DEFAULT_PORT;
        if (i < size && pText[i] == ':') {
            i++;
            port = 0;
            u32 digits = 0;
            while (i < size && isDigit(pText[i]) && digits < 5) {
                port = port * 10 + (pText[i] - '0');
                i++;
                digits++;
            }
            if (digits == 0 || port == 0 || port > 0xFFFF) {
                return false;
            }
        }

        pOut->addr = addr;
        pOut->port = (u16)port;
        return true;
    }

    void readServerAddress() {
        s32 entry = DVDConvertPathToEntrynum(SERVER_ADDRESS_FILE);
        if (entry < 0) {
            OSReport("[SMG2O] %s is missing, using 127.0.0.1\n", SERVER_ADDRESS_FILE);
            return;
        }

        DVDFileInfo file;
        if (!DVDFastOpen(entry, &file)) {
            return;
        }

        s32 size = file.mLength < sizeof(sInitBuffer) ? file.mLength : sizeof(sInitBuffer);
        size = DVDReadPrio(&file, sInitBuffer, (size + 31) & ~31, 0, 2);
        DVDClose(&file);
        if (size > (s32)file.mLength) {
            size = file.mLength;
        }

        if (size <= 0 || !parseAddress((const char *)sInitBuffer, size, &sServer)) {
            OSReport("[SMG2O] could not read a server address from %s\n", SERVER_ADDRESS_FILE);
        }
    }

    bool openSocket() {
        s32 kd = IOS_Open("/dev/net/kd/request", 0);
        if (kd < 0) {
            return false;
        }
        s32 err = IOS_Ioctl(kd, IOCTL_NWC24_STARTUP_SOCKET, NULL, 0, sInitBuffer, 32);
        IOS_Close(kd);
        if (err < 0) {
            return false;
        }

        sTopFd = IOS_Open("/dev/net/ip/top", 0);
        if (sTopFd < 0) {
            return false;
        }
        if (IOS_Ioctl(sTopFd, IOCTL_SO_STARTUP, NULL, 0, NULL, 0) < 0) {
            return false;
        }

        // Wait for the console to have an IP address
        s32 host = 0;
        for (u32 i = 0; i < 30 && host == 0; i++) {
            host = IOS_Ioctl(sTopFd, IOCTL_SO_GETHOSTID, NULL, 0, NULL, 0);
            if (host == 0) {
                sleepMs(500);
            }
        }
        if (host == 0) {
            return false;
        }

        u32 *pArgs = (u32 *)sInitBuffer;
        pArgs[0] = 2; // AF_INET
        pArgs[1] = 2; // SOCK_DGRAM
        pArgs[2] = 0;
        sSocket = IOS_Ioctl(sTopFd, IOCTL_SO_SOCKET, pArgs, 12, NULL, 0);
        return sSocket >= 0;
    }

    void *txMain(void *) {
        while (true) {
            BOOL enabled = OSDisableInterrupts();
            while (sTxCount == 0) {
                OSSleepThread(&sTxWait);
            }
            u32 size = sTxSize[sTxHead];
            memcpy(sTxBuffer, sTxData[sTxHead], size);
            sTxHead = (sTxHead + 1) % TX_SLOTS;
            sTxCount--;
            OSRestoreInterrupts(enabled);

            memset(&sTxParams, 0, sizeof(sTxParams));
            sTxParams.fd = sSocket;
            sTxParams.hasAddr = 1;
            memcpy(sTxParams.addr, &sServer, sizeof(sServer));
            sTxVectors[0].base = sTxBuffer;
            sTxVectors[0].length = size;
            sTxVectors[1].base = &sTxParams;
            sTxVectors[1].length = sizeof(sTxParams);
            IOS_Ioctlv(sTopFd, IOCTL_SO_SENDTO, 2, 0, sTxVectors);
            sHasSent = true;
        }
        return NULL;
    }

    void *rxMain(void *) {
        readServerAddress();
        if (!openSocket()) {
            OSReport("[SMG2O] network start failed\n");
            sState = STATE_FAILED;
            return NULL;
        }

        OSReport("[SMG2O] network up, server %d.%d.%d.%d:%d\n", sServer.addr >> 24, (sServer.addr >> 16) & 0xFF,
            (sServer.addr >> 8) & 0xFF, sServer.addr & 0xFF, sServer.port);

        OSCreateThread(&sTxThread, txMain, NULL, sTxStack + STACK_SIZE, STACK_SIZE, 15, 1);
        OSResumeThread(&sTxThread);
        sState = STATE_READY;

        // The socket has no local port until the first datagram went out
        while (!sHasSent) {
            sleepMs(20);
        }

        while (true) {
            sRxParams[0] = sSocket;
            sRxParams[1] = 0;
            sRxVectors[0].base = sRxParams;
            sRxVectors[0].length = 8;
            sRxVectors[1].base = sRxBuffer;
            sRxVectors[1].length = Net::MAX_DATAGRAM;
            sRxVectors[2].base = NULL;
            sRxVectors[2].length = 0;

            s32 size = IOS_Ioctlv(sTopFd, IOCTL_SO_RECVFROM, 1, 2, sRxVectors);
            if (size <= 0) {
                sleepMs(50);
                continue;
            }
            if (size > (s32)Net::MAX_DATAGRAM) {
                size = Net::MAX_DATAGRAM;
            }

            BOOL enabled = OSDisableInterrupts();
            if (sRxCount < RX_SLOTS) {
                u32 slot = (sRxHead + sRxCount) % RX_SLOTS;
                memcpy(sRxData[slot], sRxBuffer, size);
                sRxSize[slot] = (u16)size;
                sRxCount++;
            }
            OSRestoreInterrupts(enabled);
        }
        return NULL;
    }
};

namespace Net {
    void start() {
        if (sState != STATE_IDLE) {
            return;
        }
        sState = STATE_STARTING;
        OSInitThreadQueue(&sTxWait);
        OSCreateThread(&sRxThread, rxMain, NULL, sRxStack + STACK_SIZE, STACK_SIZE, 15, 1);
        OSResumeThread(&sRxThread);
    }

    bool isReady() {
        return sState == STATE_READY;
    }

    bool hasFailed() {
        return sState == STATE_FAILED;
    }

    bool send(const void *pData, u32 size) {
        if (sState != STATE_READY || size == 0 || size > MAX_DATAGRAM) {
            return false;
        }

        bool queued = false;
        BOOL enabled = OSDisableInterrupts();
        if (sTxCount < TX_SLOTS) {
            u32 slot = (sTxHead + sTxCount) % TX_SLOTS;
            memcpy(sTxData[slot], pData, size);
            sTxSize[slot] = (u16)size;
            sTxCount++;
            queued = true;
            OSWakeupThread(&sTxWait);
        }
        OSRestoreInterrupts(enabled);
        return queued;
    }

    u32 receive(void *pDest) {
        u32 size = 0;
        BOOL enabled = OSDisableInterrupts();
        if (sRxCount > 0) {
            size = sRxSize[sRxHead];
            memcpy(pDest, sRxData[sRxHead], size);
            sRxHead = (sRxHead + 1) % RX_SLOTS;
            sRxCount--;
        }
        OSRestoreInterrupts(enabled);
        return size;
    }
};
