package navi.server.identity;

/** 인증된 요청의 주인. 턴·쿼터는 전부 {@code userId}에 붙는다 — 기기가 아니다(D23). */
public record DeviceIdentity(long userId, long deviceId) {}
