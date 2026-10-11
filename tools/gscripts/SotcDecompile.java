// Headless helper for the SotC research: apply work/symbols.tsv, then decompile functions.
// Script args: SYMBOLS_TSV OUT_TXT target... (target = symbol name or 0x address)
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.app.cmd.function.CreateFunctionCmd;
import java.io.*;
import java.util.*;

public class SotcDecompile extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Map<String, Long> byName = new HashMap<>();
        Listing listing = currentProgram.getListing();
        SymbolTable st = currentProgram.getSymbolTable();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        boolean fresh = st.getSymbols("GAMEUTIL_get_player_obj").hasNext() == false;
        for (String line : java.nio.file.Files.readAllLines(new File(args[0]).toPath())) {
            String[] f = line.split("\t");
            if (f.length < 4) continue;
            long a = Long.decode(f[0]);
            byName.putIfAbsent(f[3], a);
            if (!fresh) continue;
            Address addr = space.getAddress(a);
            if (!currentProgram.getMemory().contains(addr)) continue;
            try { st.createLabel(addr, f[3], SourceType.IMPORTED); } catch (Exception e) {}
            if (f[2].equals("func") && listing.getFunctionAt(addr) == null) {
                new CreateFunctionCmd(f[3], addr, null, SourceType.IMPORTED).applyTo(currentProgram, monitor);
            }
        }
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new FileWriter(args[1]))) {
            for (int i = 2; i < args.length; i++) {
                String t = args[i];
                long a = byName.containsKey(t) ? byName.get(t) : Long.decode(t);
                Address addr = space.getAddress(a);
                Function fn = listing.getFunctionContaining(addr);
                if (fn == null) {
                    new CreateFunctionCmd(null, addr, null, SourceType.ANALYSIS).applyTo(currentProgram, monitor);
                    fn = listing.getFunctionAt(addr);
                }
                out.println("##### " + t + " @ " + addr);
                if (fn == null) { out.println("(no function)"); continue; }
                DecompileResults r = di.decompileFunction(fn, 60, monitor);
                out.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "(failed: " + r.getErrorMessage() + ")");
            }
        }
    }
}
