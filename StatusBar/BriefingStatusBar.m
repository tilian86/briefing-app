#import <Cocoa/Cocoa.h>

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSMenu *menu;
@property(nonatomic, strong) NSMenuItem *brandLine;
@property(nonatomic, strong) NSMenuItem *ownerLine;
@property(nonatomic, strong) NSMenuItem *statusLine;
@property(nonatomic, strong) NSMenuItem *remoteLine;
@property(nonatomic, strong) NSMenuItem *localCopyItem;
@property(nonatomic, strong) NSMenuItem *tailscaleCopyItem;
@property(nonatomic, strong) NSMenuItem *hostCopyItem;
@property(nonatomic, strong) NSMenuItem *openItem;
@property(nonatomic, strong) NSMenuItem *refreshItem;
@property(nonatomic, strong) NSMenuItem *restartItem;
@property(nonatomic, strong) NSMenuItem *stopItem;
@property(nonatomic, strong) NSTimer *timer;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [self setupMenu];
    [self setIndicatorColor:[NSColor secondaryLabelColor]];
    [self refreshStatus:nil];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:12.0
                                                  target:self
                                                selector:@selector(refreshStatus:)
                                                userInfo:nil
                                                 repeats:YES];
}

- (void)setupMenu {
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSSquareStatusItemLength];
    self.menu = [[NSMenu alloc] initWithTitle:@"Audio-Briefing"];

    self.brandLine = [[NSMenuItem alloc] initWithTitle:@"Audio-Briefing" action:nil keyEquivalent:@""];
    self.ownerLine = [[NSMenuItem alloc] initWithTitle:@"© Florian Sebastian Thiel" action:nil keyEquivalent:@""];
    self.statusLine = [[NSMenuItem alloc] initWithTitle:@"Status wird geprüft…" action:nil keyEquivalent:@""];
    self.remoteLine = [[NSMenuItem alloc] initWithTitle:@"Tailscale wird geprüft…" action:nil keyEquivalent:@""];
    self.localCopyItem = [[NSMenuItem alloc] initWithTitle:@"Lokale URL kopieren" action:@selector(copyLocalURL) keyEquivalent:@""];
    self.tailscaleCopyItem = [[NSMenuItem alloc] initWithTitle:@"Tailscale-URL kopieren" action:@selector(copyTailscaleURL) keyEquivalent:@""];
    self.hostCopyItem = [[NSMenuItem alloc] initWithTitle:@"Host-URL kopieren" action:@selector(copyHostURL) keyEquivalent:@""];
    self.openItem = [[NSMenuItem alloc] initWithTitle:@"App öffnen" action:@selector(openApp) keyEquivalent:@""];
    self.refreshItem = [[NSMenuItem alloc] initWithTitle:@"Status aktualisieren" action:@selector(refreshStatus:) keyEquivalent:@""];
    self.restartItem = [[NSMenuItem alloc] initWithTitle:@"App neu starten" action:@selector(restartService) keyEquivalent:@""];
    self.stopItem = [[NSMenuItem alloc] initWithTitle:@"App stoppen" action:@selector(stopService) keyEquivalent:@""];

    self.brandLine.enabled = NO;
    self.ownerLine.enabled = NO;
    self.statusLine.enabled = NO;
    self.remoteLine.enabled = NO;
    self.tailscaleCopyItem.hidden = YES;
    self.tailscaleCopyItem.enabled = NO;

    self.localCopyItem.target = self;
    self.tailscaleCopyItem.target = self;
    self.hostCopyItem.target = self;
    self.openItem.target = self;
    self.refreshItem.target = self;
    self.restartItem.target = self;
    self.stopItem.target = self;

    [self.menu addItem:self.brandLine];
    [self.menu addItem:self.ownerLine];
    [self.menu addItem:[NSMenuItem separatorItem]];
    [self.menu addItem:self.statusLine];
    [self.menu addItem:self.remoteLine];
    [self.menu addItem:[NSMenuItem separatorItem]];
    [self.menu addItem:self.openItem];
    [self.menu addItem:self.localCopyItem];
    [self.menu addItem:self.tailscaleCopyItem];
    [self.menu addItem:self.hostCopyItem];
    [self.menu addItem:self.refreshItem];
    [self.menu addItem:[NSMenuItem separatorItem]];
    [self.menu addItem:self.restartItem];
    [self.menu addItem:self.stopItem];

    self.statusItem.menu = self.menu;
    self.statusItem.button.toolTip = @"Audio-Briefing Status";
}

- (void)setIndicatorColor:(NSColor *)color {
    NSDictionary *attributes = @{
        NSForegroundColorAttributeName: color,
        NSFontAttributeName: [NSFont systemFontOfSize:13 weight:NSFontWeightBold]
    };
    self.statusItem.button.title = @"";
    self.statusItem.button.attributedTitle = [[NSAttributedString alloc] initWithString:@"●" attributes:attributes];
}

- (void)probeURL:(NSURL *)url completion:(void (^)(BOOL ok))completion {
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = @"HEAD";
    request.timeoutInterval = 2.0;

    [[[NSURLSession sharedSession] dataTaskWithRequest:request
                                     completionHandler:^(__unused NSData *data, NSURLResponse *response, NSError *error) {
        NSInteger statusCode = [(NSHTTPURLResponse *)response statusCode];
        BOOL ok = (error == nil) && (statusCode >= 200) && (statusCode <= 399);
        completion(ok);
    }] resume];
}

- (void)refreshStatus:(__unused id)sender {
    NSString *tailscaleURL = [self detectedTailscaleURL];
    NSURL *localProbeURL = [NSURL URLWithString:@"http://127.0.0.1:8501/"];

    [self probeURL:localProbeURL completion:^(BOOL localUp) {
        if (tailscaleURL.length == 0) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self applyStatusLocalUp:localUp tailscaleURL:nil tailscaleUp:nil];
            });
            return;
        }

        NSURL *tailscaleProbeURL = [NSURL URLWithString:[tailscaleURL stringByAppendingString:@"/"]];
        [self probeURL:tailscaleProbeURL completion:^(BOOL tailscaleUp) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self applyStatusLocalUp:localUp tailscaleURL:tailscaleURL tailscaleUp:@(tailscaleUp)];
            });
        }];
    }];
}

- (void)applyStatusLocalUp:(BOOL)localUp tailscaleURL:(NSString *)tailscaleURL tailscaleUp:(NSNumber *)tailscaleUp {
    [self setIndicatorColor:(localUp ? [NSColor systemGreenColor] : [NSColor systemRedColor])];
    self.statusItem.button.toolTip = localUp ? @"Audio-Briefing läuft auf Port 8501" : @"Audio-Briefing antwortet nicht auf Port 8501";
    self.statusLine.title = localUp ? @"Status: lokal ok auf :8501" : @"Status: lokal keine Antwort auf :8501";

    if (tailscaleURL.length > 0) {
        if (tailscaleUp != nil) {
            self.remoteLine.title = tailscaleUp.boolValue ? @"Tailscale: ok auf :8501" : @"Tailscale: URL da, aber keine Antwort";
        } else {
            self.remoteLine.title = @"Tailscale: URL da, Status offen";
        }
        self.tailscaleCopyItem.hidden = NO;
        self.tailscaleCopyItem.enabled = YES;
        self.tailscaleCopyItem.title = [NSString stringWithFormat:@"Tailscale-URL kopieren (%@)", tailscaleURL];
    } else {
        self.remoteLine.title = @"Tailscale: nicht verfügbar";
        self.tailscaleCopyItem.hidden = YES;
        self.tailscaleCopyItem.enabled = NO;
        self.tailscaleCopyItem.title = @"Tailscale-URL kopieren";
    }
}

- (void)runShell:(NSString *)command {
    NSTask *task = [[NSTask alloc] init];
    task.launchPath = @"/bin/zsh";
    task.arguments = @[@"-lc", command];
    @try {
        [task launch];
    } @catch (__unused NSException *exception) {
    }
}

- (NSString *)runCommand:(NSString *)launchPath arguments:(NSArray<NSString *> *)arguments {
    NSTask *task = [[NSTask alloc] init];
    NSPipe *pipe = [NSPipe pipe];
    task.launchPath = launchPath;
    task.arguments = arguments;
    task.standardOutput = pipe;
    task.standardError = [NSPipe pipe];

    @try {
        [task launch];
        [task waitUntilExit];
    } @catch (__unused NSException *exception) {
        return nil;
    }

    NSData *data = [[pipe fileHandleForReading] readDataToEndOfFile];
    if (data.length == 0) {
        return nil;
    }

    NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    NSString *trimmed = [text stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    return trimmed.length > 0 ? trimmed : nil;
}

- (NSString *)detectedHostURL {
    NSString *host = [self runCommand:@"/usr/sbin/scutil" arguments:@[@"--get", @"LocalHostName"]];
    if (host.length == 0) {
        return nil;
    }
    return [NSString stringWithFormat:@"http://%@.local:8501", host];
}

- (NSString *)detectedTailscaleURL {
    NSString *ip = [self runCommand:@"/bin/zsh" arguments:@[@"-lc", @"tailscale ip -4 2>/dev/null | head -n 1"]];
    if (ip.length == 0) {
        return nil;
    }
    return [NSString stringWithFormat:@"http://%@:8501", ip];
}

- (void)copyString:(NSString *)value {
    if (value.length == 0) {
        return;
    }
    NSPasteboard *pasteboard = [NSPasteboard generalPasteboard];
    [pasteboard clearContents];
    [pasteboard setString:value forType:NSPasteboardTypeString];
}

- (void)openApp {
    [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"http://localhost:8501"]];
}

- (void)copyLocalURL {
    [self copyString:@"http://localhost:8501"];
}

- (void)copyTailscaleURL {
    [self copyString:[self detectedTailscaleURL]];
}

- (void)copyHostURL {
    [self copyString:[self detectedHostURL]];
}

- (void)restartService {
    [self runShell:@"UID_NUM=$(id -u); launchctl kickstart -k gui/$UID_NUM/local.florian.briefing-app >/dev/null 2>&1 || true"];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self refreshStatus:nil];
    });
}

- (void)stopService {
    [self runShell:@"UID_NUM=$(id -u); launchctl bootout gui/$UID_NUM/local.florian.briefing-app >/dev/null 2>&1 || true"];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self refreshStatus:nil];
    });
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        AppDelegate *delegate = [[AppDelegate alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
