import { Ionicons } from "@expo/vector-icons";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

export function Card({ t, style, children }) {
  return <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line }, style]}>{children}</View>;
}

export function SectionTitle({ t, icon, children }) {
  return (
    <View style={styles.sectionTitle}>
      {icon ? <Ionicons name={icon} size={18} color={t.brand} /> : null}
      <Text style={[styles.sectionText, { color: t.text }]}>{children}</Text>
    </View>
  );
}

export function Empty({ t, children }) {
  return (
    <View style={[styles.empty, { borderColor: t.line, backgroundColor: t.surface }]}>
      <Text style={{ color: t.muted, fontSize: 14 }}>{children}</Text>
    </View>
  );
}

export function Loading({ t }) {
  return (
    <View style={{ paddingVertical: 60, alignItems: "center" }}>
      <ActivityIndicator color={t.brand} size="large" />
    </View>
  );
}

export function ErrorBox({ t, message, onRetry }) {
  return (
    <Card t={t} style={{ padding: 16, backgroundColor: t.redSoft, borderColor: t.redSoft }}>
      <Text style={{ color: t.red, fontWeight: "700" }}>{message}</Text>
      {onRetry ? (
        <Pressable onPress={onRetry} style={{ marginTop: 10 }}>
          <Text style={{ color: t.brand, fontWeight: "700" }}>Повторить</Text>
        </Pressable>
      ) : null}
    </Card>
  );
}

export function Lesson({ t, item, first }) {
  const replaced = item.status === "replacement";
  const cancelled = item.status === "cancelled";
  return (
    <View style={[styles.lesson, !first && { borderTopWidth: 1, borderTopColor: t.line }]}>
      {replaced || cancelled ? <View style={[styles.stripe, { backgroundColor: t.red }]} /> : null}
      <View style={[styles.num, { backgroundColor: cancelled ? t.redSoft : t.brandSoft }]}>
        <Text style={{ color: cancelled ? t.red : t.brand, fontWeight: "800", fontSize: item.number === 0 ? 11 : 16 }}>
          {item.number === 0 ? "КЧ" : item.number}
        </Text>
      </View>
      <View style={{ width: 46 }}>
        <Text style={[styles.time, { color: t.muted }]}>{item.start || ""}</Text>
        <Text style={[styles.time, { color: t.muted }]}>{item.end || ""}</Text>
      </View>
      <View style={{ flex: 1 }}>
        <Text style={[styles.subject, { color: cancelled ? t.muted : t.text }, cancelled && styles.strike]}>{item.subject}</Text>
        {!cancelled && (item.room || item.teacher) ? (
          <Text style={[styles.meta, { color: t.muted }]}>
            {[item.room ? `ауд. ${item.room}` : null, item.teacher].filter(Boolean).join(" · ")}
          </Text>
        ) : null}
        {cancelled ? <Text style={[styles.mark, { color: t.red }]}>Пара отменена</Text> : null}
        {replaced ? (
          <Text style={[styles.mark, { color: t.red }]}>
            Замена{item.instead_of ? <Text style={{ color: t.muted, fontWeight: "400", textDecorationLine: "line-through" }}>  {item.instead_of}</Text> : null}
          </Text>
        ) : null}
        {item.note ? <Text style={[styles.meta, { color: t.text, opacity: 0.85 }]}>{item.note}</Text> : null}
      </View>
    </View>
  );
}

export function Lessons({ t, lessons, empty = "Пар нет." }) {
  if (!lessons || !lessons.length) return <Empty t={t}>{empty}</Empty>;
  return (
    <Card t={t} style={{ overflow: "hidden" }}>
      {lessons.map((item, i) => (
        <Lesson key={`${item.number}-${i}`} t={t} item={item} first={i === 0} />
      ))}
    </Card>
  );
}

export function WeekSide({ week, onHero }) {
  const active = (side) => week === side;
  return (
    <View style={[styles.switch, onHero && { backgroundColor: "rgba(255,255,255,0.14)", borderColor: "rgba(255,255,255,0.22)" }]}>
      {["left", "right"].map((side) => (
        <View key={side} style={[styles.part, active(side) && { backgroundColor: "#fff" }]}>
          <Text style={{ fontSize: 12, fontWeight: "800", color: active(side) ? "#0a4fae" : "rgba(255,255,255,0.8)" }}>
            {side === "left" ? "Левая" : "Правая"}
          </Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: 18 },
  sectionTitle: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 22, marginBottom: 10 },
  sectionText: { fontSize: 17, fontWeight: "800" },
  empty: { borderWidth: 1.5, borderStyle: "dashed", borderRadius: 18, padding: 16 },
  lesson: { flexDirection: "row", gap: 12, padding: 14, alignItems: "flex-start" },
  stripe: { position: "absolute", left: 0, top: 0, bottom: 0, width: 4 },
  num: { width: 36, height: 36, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  time: { fontSize: 12, fontWeight: "600", lineHeight: 17 },
  subject: { fontSize: 15, fontWeight: "700", lineHeight: 20 },
  strike: { textDecorationLine: "line-through" },
  meta: { fontSize: 13, marginTop: 3 },
  mark: { fontSize: 12, fontWeight: "800", marginTop: 4 },
  switch: { flexDirection: "row", padding: 3, borderRadius: 999, borderWidth: 1 },
  part: { paddingVertical: 6, paddingHorizontal: 11, borderRadius: 999 },
});
