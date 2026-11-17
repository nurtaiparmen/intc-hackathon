import AsyncStorage from "@react-native-async-storage/async-storage";
import { Ionicons } from "@expo/vector-icons";
import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, useColorScheme, View } from "react-native";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";

import { ChatScreen, HomeScreen, LoginScreen, ProfileScreen, TasksScreen, WeekScreen } from "./src/screens";
import { dark, light } from "./src/theme";

const TABS = [
  { key: "home", label: "Главная", icon: "home" },
  { key: "week", label: "Неделя", icon: "calendar" },
  { key: "chat", label: "ИИ-агент", icon: "sparkles", ai: true },
  { key: "tasks", label: "Задания", icon: "book" },
  { key: "profile", label: "Профиль", icon: "person" },
];
const TITLES = { home: "Smart College", week: "Расписание", chat: "ИИ-агент", tasks: "Задания", profile: "Профиль" };

function Main() {
  const system = useColorScheme();
  const insets = useSafeAreaInsets();
  const [theme, setThemeState] = useState("system");
  const [session, setSession] = useState(undefined); // undefined — ещё читаем из памяти
  const [tab, setTab] = useState("home");
  const [pendingQuestion, setPendingQuestion] = useState(null);

  const t = (theme === "system" ? system === "dark" : theme === "dark") ? dark : light;

  useEffect(() => {
    (async () => {
      try {
        const saved = JSON.parse((await AsyncStorage.getItem("session")) || "null");
        setThemeState((await AsyncStorage.getItem("theme")) || "system");
        setSession(saved);
      } catch (e) {
        setSession(null);
      }
    })();
  }, []);

  const setTheme = (value) => { setThemeState(value); AsyncStorage.setItem("theme", value).catch(() => {}); };
  const onLogin = (token, user) => {
    const s = { token, user };
    setSession(s); setTab("home");
    AsyncStorage.setItem("session", JSON.stringify(s)).catch(() => {});
  };
  const onLogout = () => { setSession(null); AsyncStorage.removeItem("session").catch(() => {}); };
  const openChat = (q) => { setPendingQuestion(q); setTab("chat"); };

  if (session === undefined) {
    return <View style={[styles.center, { backgroundColor: t.hero }]}><ActivityIndicator color="#fff" size="large" /></View>;
  }
  if (!session) {
    return (
      <View style={{ flex: 1, paddingTop: insets.top, backgroundColor: t.hero }}>
        <StatusBar style="light" />
        <LoginScreen t={t} onLogin={onLogin} />
      </View>
    );
  }

  const props = { t, token: session.token, user: session.user, onExpired: onLogout };
  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      <StatusBar style={t.dark ? "light" : "dark"} />
      <View style={[styles.header, { paddingTop: insets.top + 8, backgroundColor: t.surface, borderBottomColor: t.line }]}>
        <View style={styles.emblemBox}><Image source={require("./assets/itc-emblem.png")} style={{ width: 34, height: 20 }} resizeMode="contain" /></View>
        <View style={{ flex: 1 }}>
          <Text style={{ color: t.text, fontWeight: "900", fontSize: 17 }}>{TITLES[tab]}</Text>
          <Text style={{ color: t.muted, fontSize: 11 }}>ИТК · г. Алматы</Text>
        </View>
        <Pressable onPress={() => setTheme(t.dark ? "light" : "dark")} style={[styles.iconBtn, { borderColor: t.line }]}>
          <Ionicons name={t.dark ? "sunny-outline" : "moon-outline"} size={20} color={t.text} />
        </Pressable>
      </View>

      <View style={{ flex: 1 }}>
        {tab === "home" && <HomeScreen {...props} openChat={openChat} />}
        {tab === "week" && <WeekScreen {...props} />}
        {tab === "tasks" && <TasksScreen {...props} />}
        {tab === "chat" && <ChatScreen {...props} pending={pendingQuestion} clearPending={() => setPendingQuestion(null)} />}
        {tab === "profile" && <ProfileScreen {...props} theme={theme} setTheme={setTheme} onLogout={onLogout} />}
      </View>

      <View style={[styles.tabbar, { paddingBottom: Math.max(insets.bottom, 8), backgroundColor: t.surface, borderTopColor: t.line }]}>
        {TABS.map((item) => {
          const active = tab === item.key;
          return (
            <Pressable key={item.key} onPress={() => setTab(item.key)} style={styles.tab}>
              {item.ai ? (
                <View style={[styles.aiBtn, { backgroundColor: t.brand }]}>
                  <Ionicons name="sparkles" size={24} color="#fff" />
                </View>
              ) : (
                <Ionicons name={active ? item.icon : `${item.icon}-outline`} size={23} color={active ? t.brand : t.muted} />
              )}
              <Text style={{ fontSize: 11, fontWeight: "700", color: active || item.ai ? t.brand : t.muted, marginTop: 3 }}>{item.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <Main />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 10, borderBottomWidth: 1 },
  emblemBox: { backgroundColor: "#fff", borderRadius: 10, paddingVertical: 6, paddingHorizontal: 6 },
  iconBtn: { width: 40, height: 40, borderRadius: 12, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  tabbar: { flexDirection: "row", borderTopWidth: 1, paddingTop: 6 },
  tab: { flex: 1, alignItems: "center", justifyContent: "flex-end" },
  aiBtn: { width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center", marginTop: -22 },
});
