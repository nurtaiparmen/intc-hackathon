import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, Image, KeyboardAvoidingView, Platform, Pressable, RefreshControl,
  ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";

import * as api from "./api";
import { Card, Empty, ErrorBox, Lessons, Loading, SectionTitle, WeekSide } from "./ui";

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const fmtDate = (iso) => {
  // «2026-11-18» — это день, а не момент времени: разбираем без часового пояса.
  const d = iso.length === 10 ? new Date(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) : new Date(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
};
// Дата ГГГГ-ММ-ДД по местному времени телефона (toISOString дал бы UTC — ночью это «вчера»).
const localISO = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmtDateTime = (iso) => {
  const d = new Date(iso);
  return `${fmtDate(iso)}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** Загрузка данных с «потянуть вниз, чтобы обновить» и выходом при протухшем токене. */
function useLoad(loader, token, onExpired, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await loader(token));
    } catch (e) {
      if (e.status === 401) onExpired();
      else setError(e.message);
    }
  }, [token, ...deps]);
  useEffect(() => { load(); }, [load]);
  const refresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };
  return { data, error, refreshing, refresh, reload: load };
}

function Screen({ t, refreshing, onRefresh, children }) {
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.bg }}
      contentContainerStyle={{ padding: 16, paddingBottom: 30 }}
      refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.brand} /> : undefined}
    >
      {children}
    </ScrollView>
  );
}

// ---------------------------------------------------------------- Вход
export function LoginScreen({ t, onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (u = username, p = password) => {
    setBusy(true); setError(null);
    try {
      const data = await api.login(u.trim(), p);
      onLogin(data.token, data.user);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1, backgroundColor: t.hero }}>
      <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: "center", padding: 22 }}>
        <View style={styles.logoBox}>
          <Image source={require("../assets/itc-logo.png")} style={{ width: 230, height: 48 }} resizeMode="contain" />
        </View>
        <Text style={styles.loginTitle}>Smart College</Text>
        <Text style={styles.loginLead}>Расписание, замены, задания и ИИ-агент колледжа — в телефоне</Text>

        <Card t={t} style={{ padding: 18, marginTop: 26 }}>
          <Text style={[styles.label, { color: t.text }]}>Логин</Text>
          <TextInput
            value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false}
            placeholder="student" placeholderTextColor={t.muted}
            style={[styles.input, { color: t.text, borderColor: t.line, backgroundColor: t.surface2 }]}
          />
          <Text style={[styles.label, { color: t.text, marginTop: 12 }]}>Пароль</Text>
          <TextInput
            value={password} onChangeText={setPassword} secureTextEntry placeholder="••••••••" placeholderTextColor={t.muted}
            style={[styles.input, { color: t.text, borderColor: t.line, backgroundColor: t.surface2 }]}
            onSubmitEditing={() => submit()}
          />
          {error ? <Text style={{ color: t.red, marginTop: 10, fontWeight: "600" }}>{error}</Text> : null}
          <Pressable onPress={() => submit()} disabled={busy} style={[styles.btn, { backgroundColor: t.brand, opacity: busy ? 0.6 : 1 }]}>
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Войти</Text>}
          </Pressable>
          <Text style={{ color: t.muted, fontSize: 12, marginTop: 14 }}>Демо (пароль demo12345):</Text>
          <View style={{ flexDirection: "row", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            {[["student", "Студент"], ["teacher", "Куратор"]].map(([u, label]) => (
              <Pressable key={u} onPress={() => { setUsername(u); setPassword("demo12345"); submit(u, "demo12345"); }}
                style={[styles.chip, { borderColor: t.line, backgroundColor: t.surface2 }]}>
                <Text style={{ color: t.text, fontWeight: "700", fontSize: 13 }}>{label}</Text>
              </Pressable>
            ))}
          </View>
        </Card>
        <Text style={styles.loginFoot}>Инновационный Технический Колледж · г. Алматы</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

// ---------------------------------------------------------------- Главная
export function HomeScreen({ t, token, user, onExpired, openChat }) {
  const { data, error, refreshing, refresh, reload } = useLoad(api.getToday, token, onExpired);
  const isTeacher = user.role === "teacher";

  const now = new Date();
  const minutes = now.getHours() * 60 + now.getMinutes();
  const toMin = (s) => (s ? Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5)) : null);
  let focus = null, focusKind = null;
  if (data && data.date === localISO(now)) {
    for (const l of data.lessons) {
      if (l.status === "cancelled") continue;
      const s = toMin(l.start), e = toMin(l.end);
      if (s !== null && s <= minutes && minutes < e) { focus = l; focusKind = "now"; break; }
      if (s !== null && s > minutes && !focus) { focus = l; focusKind = "next"; }
    }
  }

  return (
    <Screen t={t} refreshing={refreshing} onRefresh={refresh}>
      {error ? <ErrorBox t={t} message={error} onRetry={reload} /> : null}
      {!data && !error ? <Loading t={t} /> : null}
      {data ? (
        <>
          <View style={[styles.hero, { backgroundColor: t.hero }]}>
            <View style={styles.heroRing} />
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" }}>
              <View style={{ flex: 1, paddingRight: 10 }}>
                <Text style={styles.kicker}>{data.group ? `${data.group} · ` : ""}{data.week_number}-я неделя</Text>
                <Text style={styles.heroDate}>{data.weekday}, {fmtDate(data.date)}</Text>
                <Text style={styles.heroMeta}>
                  {isTeacher ? "Здравствуйте" : "Привет"}, {user.first_name || user.name}! {data.lessons.length ? `Пар сегодня: ${data.lessons.length}.` : "Сегодня пар нет."}
                </Text>
              </View>
              <WeekSide week={data.week} onHero />
            </View>
            {focus ? (
              <View style={styles.focus}>
                <Text style={styles.focusLabel}>{focusKind === "now" ? "● Идёт сейчас" : "Следующая пара"}</Text>
                <Text style={styles.focusTitle}>{focus.subject}</Text>
                <Text style={styles.focusMeta}>
                  {focus.start}–{focus.end}{focus.room ? ` · ауд. ${focus.room}` : ""}{focus.teacher ? ` · ${focus.teacher}` : ""}
                </Text>
              </View>
            ) : null}
          </View>

          {!isTeacher ? (
            <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
              <Stat t={t} value={data.debts_count} label={data.debts_count ? "долгов" : "долгов нет"} bad={data.debts_count > 0} />
              <Stat t={t} value={data.upcoming_count} label="заданий впереди" />
              <Stat t={t} value={data.lessons.length} label="пар сегодня" />
            </View>
          ) : null}

          <SectionTitle t={t} icon="sparkles">Спроси ИИ-агента</SectionTitle>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
            {(isTeacher
              ? ["Кто должники?", "Какие пары у группы завтра?", "Отмени 3 пару в пятницу"]
              : ["Какие пары завтра?", "Что мне сдать на этой неделе?", "Какая сейчас неделя?"]
            ).map((q) => (
              <Pressable key={q} onPress={() => openChat(q)} style={[styles.chip, { borderColor: t.line, backgroundColor: t.surface }]}>
                <Text style={{ color: t.text, fontWeight: "700", fontSize: 13 }}>{q}</Text>
              </Pressable>
            ))}
          </ScrollView>

          <SectionTitle t={t} icon="time-outline">Сегодня</SectionTitle>
          <Lessons t={t} lessons={data.lessons} empty="Сегодня пар нет." />
        </>
      ) : null}
    </Screen>
  );
}

function Stat({ t, value, label, bad }) {
  return (
    <Card t={t} style={[{ flex: 1, padding: 12 }, bad && { backgroundColor: t.redSoft, borderColor: t.red }]}>
      <Text style={{ fontSize: 26, fontWeight: "900", color: bad ? t.red : t.brand }}>{value}</Text>
      <Text style={{ fontSize: 12, color: t.muted, fontWeight: "600" }}>{label}</Text>
    </Card>
  );
}

// ---------------------------------------------------------------- Неделя
export function WeekScreen({ t, token, onExpired }) {
  const [offset, setOffset] = useState(0);
  const date = localISO(new Date(Date.now() + offset * 7 * 86400000));
  const { data, error, refreshing, refresh, reload } = useLoad((tk) => api.getWeek(tk, date), token, onExpired, [date]);
  const [open, setOpen] = useState({});

  return (
    <Screen t={t} refreshing={refreshing} onRefresh={refresh}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <NavBtn t={t} icon="chevron-back" onPress={() => setOffset(offset - 1)} />
        <View style={{ flex: 1, alignItems: "center" }}>
          <Text style={{ color: t.text, fontWeight: "800", fontSize: 17 }}>
            {data ? `Неделя ${data.week_number} · ${data.week === "left" ? "левая" : "правая"}` : "Неделя"}
          </Text>
          {offset !== 0 ? (
            <Pressable onPress={() => setOffset(0)}><Text style={{ color: t.brand, fontWeight: "700", marginTop: 2 }}>к текущей</Text></Pressable>
          ) : null}
        </View>
        <NavBtn t={t} icon="chevron-forward" onPress={() => setOffset(offset + 1)} />
      </View>
      {error ? <View style={{ marginTop: 14 }}><ErrorBox t={t} message={error} onRetry={reload} /></View> : null}
      {!data && !error ? <Loading t={t} /> : null}
      {data ? data.days.map((day) => {
        const isOpen = open[day.date] ?? (day.is_today || offset !== 0);
        return (
          <Card key={day.date} t={t} style={[{ marginTop: 12, overflow: "hidden" }, day.is_today && { borderColor: t.brand }]}>
            <Pressable onPress={() => setOpen({ ...open, [day.date]: !isOpen })} style={styles.dayHead}>
              <View style={[styles.dayBadge, { backgroundColor: day.is_today ? t.brand : t.surface3 }]}>
                <Text style={{ fontWeight: "900", fontSize: 16, color: day.is_today ? "#fff" : t.text }}>{Number(day.date.slice(8, 10))}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={{ color: t.text, fontWeight: "800", fontSize: 15 }}>{day.weekday}</Text>
                <Text style={{ color: t.muted, fontSize: 13 }}>{day.is_today ? "сегодня" : fmtDate(day.date)}</Text>
              </View>
              <Text style={{ color: t.muted, fontSize: 12, fontWeight: "600" }}>{day.lessons.length ? `пар: ${day.lessons.length}` : "выходной"}</Text>
              <Ionicons name={isOpen ? "chevron-up" : "chevron-down"} size={18} color={t.muted} />
            </Pressable>
            {isOpen ? (
              <View style={{ borderTopWidth: 1, borderTopColor: t.line }}>
                {day.lessons.length ? (
                  <Lessons t={{ ...t, line: t.line }} lessons={day.lessons} />
                ) : (
                  <Text style={{ color: t.muted, padding: 14 }}>В этот день пар нет.</Text>
                )}
              </View>
            ) : null}
          </Card>
        );
      }) : null}
    </Screen>
  );
}

function NavBtn({ t, icon, onPress }) {
  return (
    <Pressable onPress={onPress} style={[styles.navBtn, { borderColor: t.line, backgroundColor: t.surface }]}>
      <Ionicons name={icon} size={20} color={t.text} />
    </Pressable>
  );
}

// ---------------------------------------------------------------- Задания
export function TasksScreen({ t, token, user, onExpired }) {
  const { data, error, refreshing, refresh, reload } = useLoad(api.getHomework, token, onExpired);
  const isTeacher = user.role === "teacher";
  const Row = ({ hw, debt, first }) => (
    <View style={[{ padding: 14 }, !first && { borderTopWidth: 1, borderTopColor: t.line }]}>
      {debt ? <View style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 4, backgroundColor: t.red }} /> : null}
      <Text style={{ color: t.brand, fontSize: 11, fontWeight: "800", textTransform: "uppercase" }}>{hw.subject}</Text>
      <Text style={{ color: t.text, fontWeight: "700", fontSize: 15, marginTop: 2 }}>{hw.title}</Text>
      {hw.description ? <Text style={{ color: t.muted, fontSize: 13, marginTop: 2 }}>{hw.description}</Text> : null}
      <Text style={{ color: debt ? t.red : t.muted, fontSize: 12, fontWeight: "700", marginTop: 6 }}>
        {debt ? `Срок был ${fmtDate(hw.deadline)}` : `Сдать до ${fmtDateTime(hw.deadline)}`}
        {hw.submitted ? "  ✓ сдано" : ""}
        {isTeacher && hw.students ? `  · сдали ${hw.submitted_count} из ${hw.students}` : ""}
      </Text>
    </View>
  );
  return (
    <Screen t={t} refreshing={refreshing} onRefresh={refresh}>
      {error ? <ErrorBox t={t} message={error} onRetry={reload} /> : null}
      {!data && !error ? <Loading t={t} /> : null}
      {data && !isTeacher ? (
        <>
          {data.debts.length ? (
            <>
              <SectionTitle t={t} icon="alert-circle-outline">Мои долги</SectionTitle>
              <Card t={t} style={{ overflow: "hidden" }}>{data.debts.map((h, i) => <Row key={h.id} hw={h} debt first={i === 0} />)}</Card>
            </>
          ) : null}
          <SectionTitle t={t} icon="book-outline">Задания</SectionTitle>
          {data.upcoming.length ? (
            <Card t={t} style={{ overflow: "hidden" }}>{data.upcoming.map((h, i) => <Row key={h.id} hw={h} first={i === 0} />)}</Card>
          ) : <Empty t={t}>Предстоящих заданий нет.</Empty>}
        </>
      ) : null}
      {data && isTeacher ? (
        <>
          <SectionTitle t={t} icon="alert-circle-outline">Должники {data.group}</SectionTitle>
          {data.debtors.length ? (
            <Card t={t} style={{ overflow: "hidden" }}>
              {data.debtors.map((d, i) => (
                <View key={d.student} style={[{ padding: 14 }, i > 0 && { borderTopWidth: 1, borderTopColor: t.line }]}>
                  <Text style={{ color: t.text, fontWeight: "800" }}>{d.student}</Text>
                  {d.debts.map((h) => <Text key={h.id} style={{ color: t.muted, fontSize: 13, marginTop: 2 }}>{h.subject}: {h.title}</Text>)}
                </View>
              ))}
            </Card>
          ) : <Empty t={t}>Долгов нет.</Empty>}
          <SectionTitle t={t} icon="book-outline">Задания группы</SectionTitle>
          <Card t={t} style={{ overflow: "hidden" }}>{data.homework.map((h, i) => <Row key={h.id} hw={h} debt={h.overdue && h.submitted_count < h.students} first={i === 0} />)}</Card>
        </>
      ) : null}
    </Screen>
  );
}

// ---------------------------------------------------------------- ИИ-агент
export function ChatScreen({ t, token, user, onExpired, pending, clearPending }) {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const scroll = useRef(null);

  const send = async (question) => {
    const q = (question ?? text).trim();
    if (!q || busy) return;
    setText("");
    const history = messages.filter((m) => m.role !== "error").slice(-6).map(({ role, text: tx }) => ({ role, text: tx }));
    setMessages((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const r = await api.askAgent(token, q, history);
      setMessages((m) => [...m, { role: "assistant", text: r.answer, actions: r.actions, refused: r.refused, offline: r.offline }]);
    } catch (e) {
      if (e.status === 401) onExpired();
      setMessages((m) => [...m, { role: "error", text: e.message }]);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (pending) { send(pending); clearPending(); }
  }, [pending]);

  const chips = user.role === "teacher"
    ? ["Кто должники?", "Пары группы завтра", "Задай эссе по архитектуре ПО до понедельника"]
    : ["Какие пары завтра?", "Есть у меня долги?", "Оцени, насколько тяжёлый четверг"];

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: t.bg }} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={90}>
      <ScrollView ref={scroll} contentContainerStyle={{ padding: 16, gap: 10 }} onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}>
        <Card t={t} style={{ padding: 14 }}>
          <Text style={{ color: t.text, fontWeight: "800", fontSize: 15 }}>
            {user.role === "teacher" ? `Здравствуйте, ${user.first_name}!` : `Сәлем, ${user.first_name}! 👋`}
          </Text>
          <Text style={{ color: t.muted, marginTop: 4 }}>
            {user.role === "teacher"
              ? "Пишите обычными словами — я поставлю замену, отменю пару, задам задание или отмечу, кто сдал."
              : "Спрашивай про пары, замены, задания и долги — можно своими словами и на казахском."}
          </Text>
        </Card>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          {chips.map((c) => (
            <Pressable key={c} onPress={() => send(c)} style={[styles.chip, { borderColor: t.line, backgroundColor: t.surface }]}>
              <Text style={{ color: t.text, fontWeight: "700", fontSize: 13 }}>{c}</Text>
            </Pressable>
          ))}
        </View>
        {messages.map((m, i) => (
          <View key={i} style={[
            styles.msg,
            m.role === "user"
              ? { alignSelf: "flex-end", backgroundColor: t.brand, borderBottomRightRadius: 6 }
              : { alignSelf: "flex-start", backgroundColor: m.role === "error" || m.refused ? t.redSoft : t.surface, borderColor: t.line, borderWidth: 1, borderBottomLeftRadius: 6 },
          ]}>
            <Text style={{ color: m.role === "user" ? t.onBrand : m.role === "error" ? t.red : t.text, fontSize: 15, lineHeight: 21 }}>{m.text}</Text>
            {m.actions && m.actions.length ? (
              <View style={[styles.actions, { backgroundColor: t.greenSoft }]}>
                {m.actions.map((a) => <Text key={a} style={{ color: t.green, fontWeight: "700", fontSize: 13 }}>✓ {a}</Text>)}
              </View>
            ) : null}
            {m.offline ? <Text style={{ color: t.muted, fontSize: 11, marginTop: 4 }}>без ИИ</Text> : null}
          </View>
        ))}
        {busy ? (
          <View style={[styles.msg, { alignSelf: "flex-start", backgroundColor: t.surface, borderColor: t.line, borderWidth: 1 }]}>
            <ActivityIndicator color={t.brand} />
          </View>
        ) : null}
      </ScrollView>
      <View style={[styles.composer, { borderTopColor: t.line, backgroundColor: t.surface }]}>
        <TextInput
          value={text} onChangeText={setText} placeholder={user.role === "teacher" ? "Например: отмени 3 пару в пятницу" : "Спроси что-нибудь…"}
          placeholderTextColor={t.muted} onSubmitEditing={() => send()} returnKeyType="send" maxLength={500}
          style={[styles.input, { flex: 1, marginTop: 0, color: t.text, borderColor: t.line, backgroundColor: t.surface2 }]}
        />
        <Pressable onPress={() => send()} style={[styles.sendBtn, { backgroundColor: t.brand }]}>
          <Ionicons name="send" size={18} color="#fff" />
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

// ---------------------------------------------------------------- Профиль
export function ProfileScreen({ t, user, theme, setTheme, onLogout }) {
  const Row = ({ k, v, first }) => (
    <View style={[styles.kv, !first && { borderTopWidth: 1, borderTopColor: t.line }]}>
      <Text style={{ color: t.muted }}>{k}</Text>
      <Text style={{ color: t.text, fontWeight: "700", flexShrink: 1, textAlign: "right" }}>{v}</Text>
    </View>
  );
  const initials = `${(user.first_name || "")[0] || ""}${(user.last_name || "")[0] || ""}`;
  return (
    <Screen t={t}>
      <Card t={t} style={{ overflow: "hidden" }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 14, padding: 16 }}>
          <View style={[styles.avatar, { backgroundColor: t.brand }]}><Text style={{ color: "#fff", fontWeight: "900", fontSize: 18 }}>{initials}</Text></View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: t.text, fontWeight: "900", fontSize: 18 }}>{user.name}</Text>
            <Text style={{ color: t.muted }}>{user.role === "teacher" ? "Преподаватель" : `Студент · ${user.group?.name || ""}`}</Text>
          </View>
        </View>
        <View style={{ borderTopWidth: 1, borderTopColor: t.line }}>
          <Row first k="Колледж" v="Инновационный Технический Колледж" />
          {user.group ? <Row k="Группа" v={`${user.group.name}, ${user.group.course} курс`} /> : null}
          {user.group ? <Row k="Куратор" v={user.group.curators.join(", ") || "—"} /> : null}
          {user.groups ? <Row k="Группы" v={user.groups.map((g) => g.name).join(", ")} /> : null}
        </View>
      </Card>
      <Card t={t} style={{ marginTop: 16, overflow: "hidden" }}>
        {[["system", "Как в системе"], ["light", "Светлая тема"], ["dark", "Тёмная тема"]].map(([key, label], i) => (
          <Pressable key={key} onPress={() => setTheme(key)} style={[styles.menuRow, i > 0 && { borderTopWidth: 1, borderTopColor: t.line }]}>
            <Ionicons name={key === "dark" ? "moon-outline" : key === "light" ? "sunny-outline" : "phone-portrait-outline"} size={20} color={t.brand} />
            <Text style={{ color: t.text, fontWeight: "700", flex: 1 }}>{label}</Text>
            {theme === key ? <Ionicons name="checkmark" size={20} color={t.brand} /> : null}
          </Pressable>
        ))}
      </Card>
      <Card t={t} style={{ marginTop: 16, overflow: "hidden" }}>
        <Pressable onPress={onLogout} style={styles.menuRow}>
          <Ionicons name="log-out-outline" size={20} color={t.red} />
          <Text style={{ color: t.red, fontWeight: "800" }}>Выйти</Text>
        </Pressable>
      </Card>
      <Text style={{ color: t.muted, textAlign: "center", marginTop: 22, fontSize: 12 }}>
        Smart College · NeuroHack 2026 · РПО6-24Р{"\n"}{api.API_URL.replace("/api", "")}
      </Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  logoBox: { alignSelf: "flex-start", backgroundColor: "#fff", borderRadius: 16, paddingVertical: 10, paddingHorizontal: 14 },
  loginTitle: { color: "#fff", fontSize: 38, fontWeight: "900", marginTop: 26, letterSpacing: -1 },
  loginLead: { color: "rgba(255,255,255,0.85)", fontSize: 16, marginTop: 6, lineHeight: 22 },
  loginFoot: { color: "rgba(255,255,255,0.6)", textAlign: "center", marginTop: 22, fontSize: 12 },
  label: { fontWeight: "800", fontSize: 13, marginBottom: 6 },
  input: { borderWidth: 1, borderRadius: 14, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15 },
  btn: { marginTop: 16, borderRadius: 14, paddingVertical: 14, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "800", fontSize: 16 },
  chip: { borderWidth: 1, borderRadius: 999, paddingVertical: 9, paddingHorizontal: 14 },
  hero: { borderRadius: 24, padding: 18, overflow: "hidden" },
  heroRing: { position: "absolute", width: 420, height: 150, right: -150, top: -30, borderRadius: 200, borderWidth: 18, borderColor: "rgba(255,255,255,0.07)", transform: [{ rotate: "-12deg" }] },
  kicker: { color: "rgba(255,255,255,0.75)", fontSize: 11, fontWeight: "800", letterSpacing: 1, textTransform: "uppercase" },
  heroDate: { color: "#fff", fontSize: 26, fontWeight: "900", marginTop: 4, letterSpacing: -0.5 },
  heroMeta: { color: "rgba(255,255,255,0.85)", marginTop: 4, fontSize: 14 },
  focus: { marginTop: 14, padding: 14, borderRadius: 16, backgroundColor: "rgba(255,255,255,0.12)", borderWidth: 1, borderColor: "rgba(255,255,255,0.18)" },
  focusLabel: { color: "#ffd23f", fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: 0.8 },
  focusTitle: { color: "#fff", fontSize: 17, fontWeight: "800", marginTop: 4 },
  focusMeta: { color: "rgba(255,255,255,0.88)", fontSize: 13, marginTop: 4 },
  navBtn: { width: 42, height: 42, borderRadius: 12, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  dayHead: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14 },
  dayBadge: { width: 40, height: 40, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  msg: { maxWidth: "86%", paddingVertical: 10, paddingHorizontal: 14, borderRadius: 18 },
  actions: { marginTop: 8, padding: 10, borderRadius: 10, gap: 4 },
  composer: { flexDirection: "row", gap: 8, padding: 10, borderTopWidth: 1, alignItems: "center" },
  sendBtn: { width: 48, height: 48, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  kv: { flexDirection: "row", justifyContent: "space-between", gap: 12, paddingHorizontal: 16, paddingVertical: 13 },
  menuRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16 },
  avatar: { width: 54, height: 54, borderRadius: 18, alignItems: "center", justifyContent: "center" },
});
