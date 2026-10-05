#pragma once

#include "revolution.h"

// UDP transport. All IOS calls happen on two background threads, so the game
// thread never waits for the network: it only copies datagrams in and out of
// small queues.
namespace Net {
    // Largest datagram in either direction. Must stay a multiple of 32.
    const u32 MAX_DATAGRAM = 1280;

    // Start the network threads. Returns at once; isReady() turns true when
    // the socket is up and the server address has been read.
    void start();
    bool isReady();
    bool hasFailed();

    // Game thread: queue a datagram for the server. False if the queue is full.
    bool send(const void *pData, u32 size);

    // Game thread: copy the oldest received datagram to pDest (at least
    // MAX_DATAGRAM bytes). Returns its size, or 0 if there is none.
    u32 receive(void *pDest);
};
