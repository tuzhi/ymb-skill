package benchmark;

import com.sun.management.HotSpotDiagnosticMXBean;

import java.io.BufferedReader;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.Comparator;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class TransactionMemoryBenchmark {
    private static final int DEFAULT_TARGET_ROWS = 20_000;

    public enum AccountType { CORPORATE, PERSONAL, UNKNOWN }
    public enum Direction { INCOME, EXPENSE, UNKNOWN }
    public enum TransactionStatus { NORMAL, REVERSAL, UNKNOWN }

    public record AccountDTO(
            String ownerName,
            String accountNumber,
            String bankName,
            AccountType accountType
    ) {}

    public record SourceFileDTO(
            String sourceFileName,
            String routerBank,
            String inferredBank,
            String bankSource,
            boolean batchPair
    ) {}

    public record TagDefinitionDTO(
            String level1,
            String level2,
            String level3,
            String tagSource,
            float confidence,
            String ruleId,
            String hitKeyword,
            String hitField
    ) {}

    public static final class TransactionDTO {
        final String transactionId;
        final long transactionTimeMillis;
        final AccountDTO account;
        final SourceFileDTO sourceFile;
        final TagDefinitionDTO tag;
        final String counterpartyName;
        final String counterpartyAccount;
        final long incomeCent;
        final long expenseCent;
        final long balanceCent;
        final long analysisIncomeCent;
        final long analysisExpenseCent;
        final int amountPresenceMask;
        final String bankRemark;
        final String accountRemark;
        final String channel;
        final Direction direction;
        final TransactionStatus status;
        final int sourceRowNumber;
        final String relatedReversalTransactionId;

        TransactionDTO(
                String transactionId,
                long transactionTimeMillis,
                AccountDTO account,
                SourceFileDTO sourceFile,
                TagDefinitionDTO tag,
                String counterpartyName,
                String counterpartyAccount,
                long incomeCent,
                long expenseCent,
                long balanceCent,
                long analysisIncomeCent,
                long analysisExpenseCent,
                int amountPresenceMask,
                String bankRemark,
                String accountRemark,
                String channel,
                Direction direction,
                TransactionStatus status,
                int sourceRowNumber,
                String relatedReversalTransactionId
        ) {
            this.transactionId = transactionId;
            this.transactionTimeMillis = transactionTimeMillis;
            this.account = account;
            this.sourceFile = sourceFile;
            this.tag = tag;
            this.counterpartyName = counterpartyName;
            this.counterpartyAccount = counterpartyAccount;
            this.incomeCent = incomeCent;
            this.expenseCent = expenseCent;
            this.balanceCent = balanceCent;
            this.analysisIncomeCent = analysisIncomeCent;
            this.analysisExpenseCent = analysisExpenseCent;
            this.amountPresenceMask = amountPresenceMask;
            this.bankRemark = bankRemark;
            this.accountRemark = accountRemark;
            this.channel = channel;
            this.direction = direction;
            this.status = status;
            this.sourceRowNumber = sourceRowNumber;
            this.relatedReversalTransactionId = relatedReversalTransactionId;
        }

        public long transactionAmountCent() {
            return incomeCent - expenseCent;
        }

        public long analysisTransactionAmountCent() {
            return analysisIncomeCent - analysisExpenseCent;
        }
    }

    private record AccountKey(String owner, String number, String bank, String type) {}
    private record SourceKey(String file, String router, String inferred, String source, String pair) {}
    private record TagKey(String l1, String l2, String l3, String source, String confidence,
                          String rule, String keyword, String field) {}

    private record Dataset(
            List<TransactionDTO> transactions,
            int accounts,
            int sources,
            int tags
    ) {}

    private record Measurement(long bytes, long objects, Map<Class<?>, ClassStat> byClass) {}
    private record ClassStat(long count, long shallowBytes) {
        ClassStat add(long bytes) {
            return new ClassStat(count + 1, shallowBytes + bytes);
        }
    }

    private TransactionMemoryBenchmark() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1 || args.length > 2) {
            System.err.println("Usage: TransactionMemoryBenchmark <tagged.csv> [targetRows]");
            System.exit(2);
        }
        Path csv = Path.of(args[0]);
        int targetRows = args.length == 2 ? Integer.parseInt(args[1]) : DEFAULT_TARGET_ROWS;
        List<Map<String, String>> seeds = readCsv(csv);
        if (seeds.isEmpty()) {
            throw new IllegalArgumentException("CSV contains no data rows: " + csv);
        }

        Dataset dataset = buildDataset(seeds, targetRows);
        Measurement measurement = deepMeasure(dataset.transactions());

        System.out.printf("Java: %s%n", System.getProperty("java.version"));
        System.out.printf("UseCompressedOops: %s%n", vmOption("UseCompressedOops"));
        System.out.printf("UseCompressedClassPointers: %s%n", vmOption("UseCompressedClassPointers"));
        System.out.printf("Input sample rows: %,d%n", seeds.size());
        System.out.printf("Generated TransactionDTO rows: %,d%n", dataset.transactions().size());
        System.out.printf("Shared AccountDTO: %,d%n", dataset.accounts());
        System.out.printf("Shared SourceFileDTO: %,d%n", dataset.sources());
        System.out.printf("Shared TagDefinitionDTO: %,d%n", dataset.tags());
        System.out.printf("Retained graph objects: %,d%n", measurement.objects());
        System.out.printf("Retained graph bytes: %,d%n", measurement.bytes());
        System.out.printf("Retained graph MiB: %.3f%n", measurement.bytes() / 1024.0 / 1024.0);
        System.out.printf("Bytes per transaction: %.1f%n", measurement.bytes() / (double) targetRows);
        System.out.println("Top retained shallow-size classes:");
        measurement.byClass().entrySet().stream()
                .sorted(Map.Entry.<Class<?>, ClassStat>comparingByValue(
                        Comparator.comparingLong(ClassStat::shallowBytes)).reversed())
                .limit(12)
                .forEach(entry -> System.out.printf(
                        "  %-55s objects=%,10d bytes=%,12d%n",
                        entry.getKey().getTypeName(), entry.getValue().count(), entry.getValue().shallowBytes()));
    }

    private static Dataset buildDataset(List<Map<String, String>> seeds, int targetRows) {
        Map<AccountKey, AccountDTO> accounts = new HashMap<>();
        Map<SourceKey, SourceFileDTO> sources = new HashMap<>();
        Map<TagKey, TagDefinitionDTO> tags = new HashMap<>();
        Map<String, String> lowCardinalityStrings = new HashMap<>();
        List<TransactionDTO> transactions = new ArrayList<>(targetRows);

        for (int i = 0; i < targetRows; i++) {
            Map<String, String> row = seeds.get(i % seeds.size());
            AccountKey accountKey = new AccountKey(
                    value(row, "本方名称"), value(row, "本方账户"), value(row, "开户行"), value(row, "账户类型"));
            AccountDTO account = accounts.computeIfAbsent(accountKey, key -> new AccountDTO(
                    copy(key.owner()), copy(key.number()), copy(key.bank()), accountType(key.type())));

            SourceKey sourceKey = new SourceKey(
                    value(row, "来源文件名"), value(row, "router_bank"), value(row, "inferred_bank"),
                    value(row, "bank_source"), value(row, "batch_pair"));
            SourceFileDTO source = sources.computeIfAbsent(sourceKey, key -> new SourceFileDTO(
                    copy(key.file()), copy(key.router()), copy(key.inferred()), copy(key.source()),
                    !key.pair().isBlank()));

            TagKey tagKey = new TagKey(
                    value(row, "一级标签"), value(row, "二级标签"), value(row, "三级标签"),
                    value(row, "标签来源"), value(row, "标签置信度"), value(row, "命中规则编号"),
                    value(row, "命中关键词"), value(row, "命中字段"));
            TagDefinitionDTO tag = tags.computeIfAbsent(tagKey, key -> new TagDefinitionDTO(
                    copy(key.l1()), copy(key.l2()), copy(key.l3()), copy(key.source()),
                    parseFloat(key.confidence()), copy(key.rule()), copy(key.keyword()), copy(key.field())));

            String originalId = value(row, "交易唯一编号");
            String transactionId = copy(originalId + "#" + i);
            int presenceMask = presenceMask(row, "收入金额", "支出金额", "账户余额", "分析收入金额", "分析支出金额");
            transactions.add(new TransactionDTO(
                    transactionId,
                    value(row, "交易时间").hashCode(),
                    account,
                    source,
                    tag,
                    copy(value(row, "对手名称")),
                    copy(value(row, "对手账户")),
                    cents(value(row, "收入金额")),
                    cents(value(row, "支出金额")),
                    cents(value(row, "账户余额")),
                    cents(value(row, "分析收入金额")),
                    cents(value(row, "分析支出金额")),
                    presenceMask,
                    copy(value(row, "银行备注")),
                    copy(value(row, "账户方附言")),
                    canonical(lowCardinalityStrings, value(row, "交易渠道")),
                    direction(value(row, "收支方向")),
                    status(value(row, "交易状态")),
                    integer(value(row, "来源行号")),
                    copy(value(row, "关联冲正交易编号"))));
        }
        return new Dataset(transactions, accounts.size(), sources.size(), tags.size());
    }

    private static Measurement deepMeasure(Object root) throws ReflectiveOperationException {
        IdentityHashMap<Object, Boolean> visited = new IdentityHashMap<>();
        ArrayDeque<Object> stack = new ArrayDeque<>();
        Map<Class<?>, ClassStat> byClass = new LinkedHashMap<>();
        stack.push(root);
        long total = 0;

        while (!stack.isEmpty()) {
            Object object = stack.pop();
            if (object == null || visited.put(object, Boolean.TRUE) != null) {
                continue;
            }
            Class<?> type = object.getClass();
            long shallow = SizeAgent.instrumentation().getObjectSize(object);
            total += shallow;
            byClass.compute(type, (ignored, stat) -> stat == null ? new ClassStat(1, shallow) : stat.add(shallow));

            if (type.isArray()) {
                if (!type.getComponentType().isPrimitive()) {
                    int length = Array.getLength(object);
                    for (int i = 0; i < length; i++) {
                        Object value = Array.get(object, i);
                        if (value != null) stack.push(value);
                    }
                }
                continue;
            }
            if (object instanceof Class<?> || object instanceof ClassLoader || object instanceof Enum<?>) {
                continue;
            }
            for (Class<?> current = type; current != null; current = current.getSuperclass()) {
                for (Field field : current.getDeclaredFields()) {
                    if (Modifier.isStatic(field.getModifiers()) || field.getType().isPrimitive()) {
                        continue;
                    }
                    field.setAccessible(true);
                    Object value = field.get(object);
                    if (value != null) stack.push(value);
                }
            }
        }
        return new Measurement(total, visited.size(), byClass);
    }

    private static List<Map<String, String>> readCsv(Path path) throws IOException {
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            List<String> header = readRecord(reader);
            if (header == null) return List.of();
            if (!header.isEmpty() && header.get(0).startsWith("\ufeff")) {
                header.set(0, header.get(0).substring(1));
            }
            List<Map<String, String>> rows = new ArrayList<>();
            List<String> record;
            while ((record = readRecord(reader)) != null) {
                if (record.size() == 1 && record.get(0).isEmpty()) continue;
                Map<String, String> row = new HashMap<>();
                for (int i = 0; i < header.size(); i++) {
                    row.put(header.get(i), i < record.size() ? record.get(i) : "");
                }
                rows.add(row);
            }
            return rows;
        }
    }

    private static List<String> readRecord(BufferedReader reader) throws IOException {
        List<String> fields = new ArrayList<>();
        StringBuilder field = new StringBuilder();
        boolean quoted = false;
        boolean any = false;
        while (true) {
            int raw = reader.read();
            if (raw < 0) {
                if (!any && fields.isEmpty() && field.isEmpty()) return null;
                fields.add(field.toString());
                return fields;
            }
            any = true;
            char ch = (char) raw;
            if (quoted) {
                if (ch == '"') {
                    reader.mark(1);
                    int next = reader.read();
                    if (next == '"') field.append('"');
                    else {
                        quoted = false;
                        if (next >= 0) reader.reset();
                    }
                } else {
                    field.append(ch);
                }
            } else if (ch == '"' && field.isEmpty()) {
                quoted = true;
            } else if (ch == ',') {
                fields.add(field.toString());
                field.setLength(0);
            } else if (ch == '\n') {
                fields.add(field.toString());
                return fields;
            } else if (ch != '\r') {
                field.append(ch);
            }
        }
    }

    private static String value(Map<String, String> row, String field) {
        String value = row.get(field);
        return value == null ? "" : value;
    }

    private static String copy(String value) {
        return value == null || value.isEmpty() ? null : new String(value.toCharArray());
    }

    private static String canonical(Map<String, String> pool, String value) {
        if (value == null || value.isEmpty()) return null;
        return pool.computeIfAbsent(value, ignored -> copy(value));
    }

    private static long cents(String value) {
        if (value == null || value.isBlank()) return 0L;
        try {
            return new BigDecimal(value.replace(",", "")).movePointRight(2).longValueExact();
        } catch (ArithmeticException | NumberFormatException ignored) {
            return 0L;
        }
    }

    private static int integer(String value) {
        if (value == null || value.isBlank()) return 0;
        try {
            return new BigDecimal(value).intValue();
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private static float parseFloat(String value) {
        if (value == null || value.isBlank()) return 0F;
        try {
            return Float.parseFloat(value);
        } catch (NumberFormatException ignored) {
            return 0F;
        }
    }

    private static int presenceMask(Map<String, String> row, String... fields) {
        int mask = 0;
        for (int i = 0; i < fields.length; i++) {
            if (!value(row, fields[i]).isBlank()) mask |= 1 << i;
        }
        return mask;
    }

    private static AccountType accountType(String value) {
        return switch (value) {
            case "对公" -> AccountType.CORPORATE;
            case "个人" -> AccountType.PERSONAL;
            default -> AccountType.UNKNOWN;
        };
    }

    private static Direction direction(String value) {
        return switch (value) {
            case "收入" -> Direction.INCOME;
            case "支出" -> Direction.EXPENSE;
            default -> Direction.UNKNOWN;
        };
    }

    private static TransactionStatus status(String value) {
        if (value == null || value.isBlank() || "正常".equals(value)) return TransactionStatus.NORMAL;
        if (value.contains("冲正") || value.contains("撤销")) return TransactionStatus.REVERSAL;
        return TransactionStatus.UNKNOWN;
    }

    private static String vmOption(String name) {
        try {
            HotSpotDiagnosticMXBean bean = ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class);
            return bean.getVMOption(name).getValue();
        } catch (RuntimeException ignored) {
            return "unknown";
        }
    }
}
