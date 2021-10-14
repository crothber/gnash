import sys
import chess
from reconchess import *
from game.BeliefState import *
from game.Stash import *
from strategy.select_sense import select_sense
from strategy.select_move import select_move
from helper_bot import HelperBot
import chess.engine
from utils.exceptions import EmptyBoardDist
from utils.util import *
from utils.history_utils import *
import time
import datetime
import requests

##TODO: Bonus to positions where king has few empty squares next to it
##TODO: To play quickly, add "quick_handle_opp_move":
#          get n stockfish moves for likely boards and (for opp) check! moves for unlikely boards
##TODO: If they could have made a good move but didn't, make that board less likely
##TODO: Fix bug where we took their king (but weren't sure we would), and unstash boards after we capture it
##TODO: Combine oppMoveResultUpdate and senseUpdate?
##TODO: Add boardDist evaluation
##TODO: extra points for lots of possible moves, keep good pieces in different sensing zones?
##Something to try: use attack-bot moves until a capture on the first two rows?
##TODO: Add MoveSelector class that can be instantiated (one for us, one for them (in beliefState))
class GnashBot(Player):

    def __init__(self, useQuickMoveDist=False, isTest = False):
        self.color = None
        self.board = None
        self.beliefState = None
        self.firstTurn = True
        self.moveStartTime = None
        self.isTest = isTest
        self.useQuickMoveDist= useQuickMoveDist
        self.helperBot = HelperBot()
        self.useHelperBot = False
        self.useHelperBotTime = 120
        self.turn = 0

    def handle_game_start(self, color: Color, board: chess.Board, opponent_name: str):
        self.color, self.board, self.opponent_name = color, board, opponent_name
        print(f"PLAYING {opponent_name} AS {'WHITE' if color else 'BLACK'}! Let's go!")

        now = datetime.datetime.now()
        # gameTimeStr = f"{now.date()}_{now.hour}_{now.minute}_{now.second}"
        # if not self.isTest and opponent_name not in {"moveFinder", "senseFinder"}:
            # sys.stdout = open(f"gameLogs/{opponent_name}_{gameTimeStr}.txt","w")

        self.stash = Stash(self.color)

        # self.baseurl = "http://127.0.0.1:5000"
        # self.gameId = hash(gameTimeStr)

        # requests.post(f"{self.baseurl}/start/{self.gameId}", json={"color":self.color})
 
        self.beliefState = BeliefState(color, board.fen())

        self.gameEndTime = time.time() + 900
        if opponent_name in {"Oracle", "StrangeFish2"}:
            self.useQuickMoveDist = True
        if opponent_name in {"random", "RandomBot"}:
            self.set_gear(4 if not self.isTest else 3)
        else:
            self.set_gear(0)

        #scale of one to ten
        self.aggressiveness = 1 
        self.oppAgressiveness = 3
        if opponent_name in {"attacker", "AttackBot"}:
            self.oppAgressiveness = 10
        if opponent_name in {"penumbra"}:
            self.oppAgressiveness = 7
        if opponent_name in {"Fianchetto"}:
            self.oppAgressiveness = 5
        if opponent_name in {"Oracle", "StrangeFish2"}:
            self.oppAgressiveness = 2

    def set_gear(self, gear):
        self.gear = gear
        if gear == 0:
            self.handleOppMoveMaxTime = 12
            self.handleSenseMaxTime = 5
            self.handleMoveMaxTime = 3
            self.chooseMoveMaxTime = 5
            self.maxInDist = 150
        if gear == 1:
            print("Picking up speed...")
            self.handleOppMoveMaxTime = 9
            self.handleSenseMaxTime = 3
            self.handleMoveMaxTime = 1
            self.chooseMoveMaxTime = 3
            self.maxInDist = 50
        if gear == 2:
            print("Faster and faster...")
            self.handleOppMoveMaxTime = 6
            self.handleSenseMaxTime = 2
            self.handleMoveMaxTime = .5
            self.chooseMoveMaxTime = 2
            self.maxInDist = 30
        if gear == 3:
            print("Full speed ahead!")
            self.handleOppMoveMaxTime = 4
            self.handleSenseMaxTime = 1
            self.handleMoveMaxTime = .5
            self.chooseMoveMaxTime = 1
            self.maxInDist = 2
        if gear == 4:
            self.maxInDist = 1
            print("Helper bot taking over to speed things up...")
            self.useHelperBot = True
            mostLikelyBoard = max(self.beliefState.myBoardDist, key=self.beliefState.myBoardDist.get)
            self.helperBot.handle_game_start(self.color, chess.Board(mostLikelyBoard), self.opponent_name)

    def updateSpeed(self):
        timeLeft = self.gameEndTime - time.time()
        if timeLeft <= self.useHelperBotTime and self.gear < 4:
            self.set_gear(4)

    def stash_and_add_history(self, phase : Phase, turn : int, history : tuple):
        boardsToKeep = list(sorted(self.beliefState.myBoardDist, key = self.beliefState.myBoardDist.get, reverse=True))[:self.maxInDist]
        boardsToStash = set(self.beliefState.myBoardDist).difference(boardsToKeep)
        for board in boardsToStash:
            self.beliefState.oppBoardDists.pop(board)
            self.beliefState.myBoardDist.pop(board)
        normalize(self.beliefState.myBoardDist, adjust=True, giveToZeros=0)
        print("Sending new boards...")
        requests.post(f"{self.baseurl}/stash_boards/{self.gameId}/{turn}/{phase.value}", json = {"boardsToStash": list(boardsToStash)})
        print("Sending new boards completed.")

        print("Sending new history...")
        if phase == Phase.OPP_MOVE_RESULT:
            json={"capMyPiece":history[0], "capSquare":history[1]}
            requests.post(f"{self.baseurl}/add_opp_move_result/{self.gameId}/{turn}/{phase.value}", json=json)
        if phase == Phase.SENSE_RESULT:
            senseResults = history
            json={"squares":[x[0] for x in senseResults], "pieces":[x[1].symbol() if x[1] is not None else None for x in senseResults]}
            requests.post(f"{self.baseurl}/add_sense_result/{self.gameId}/{turn}/{phase.value}", json=json)
        if phase == Phase.OUR_MOVE_RESULT:
            json={"reqMove": history[0].uci() if history[0] is not None else None, "takMove": history[1].uci() if history[1] is not None else None, "capOppPiece":history[2], "capSquare":history[3]}
            requests.post(f"{self.baseurl}/add_our_move_result/{self.gameId}/{turn}/{phase.value}", json=json)
        print("Sending new history completed.")

    def get_new_boards(self):
        self.stash.add_possible_boards(self.beliefState, self.maxInDist)        
        # print("Sending request for new boards...")
        # result = requests.post(f"{self.baseurl}/get_possible_boards/{self.gameId}", json={"numBoards":self.maxInDist})
        # boards = result.json()["fens"]
        # self.beliefState.myBoardDist = {b: 1/len(boards) for b in boards}
        # self.beliefState.oppBoardDists = {b: {b:1.0} for b in boards}
        # print(f"Received {len(boards)} boards in response")
        # self.beliefState._check_invariants()

    def handle_opponent_move_result(self, captured_my_piece: bool, capture_square: Optional[Square]):
        self.updateSpeed() 
        
        phase, turn = Phase.OPP_MOVE_RESULT, self.turn
        # self.stash_and_add_history(phase, turn, (captured_my_piece, capture_square))
        self.stash.stash_boards(phase, turn, self.beliefState, self.maxInDist)
        self.stash.add_history(turn, phase, (captured_my_piece, capture_square))

        if self.firstTurn and self.color: self.firstTurn = False; return

        print('\nOpponent moved, handling result...')
        if captured_my_piece: print(f"They captured a piece on {str(capture_square)}!")

        t0 = time.time()
        if self.useHelperBot: self.helperBot.handle_opponent_move_result(captured_my_piece, capture_square); return
        try:
            self.beliefState.opp_move_result_update(captured_my_piece, capture_square, maxTime=self.handleOppMoveMaxTime)
        except EmptyBoardDist:
            self.get_new_boards()
        print(f"Handled opponent move result in {time.time() - t0} seconds.")

    def choose_sense(self, sense_actions: List[Square], move_actions: List[chess.Move], seconds_left: float) -> \
            Optional[Square]:
        self.gameEndTime = time.time() + seconds_left
        self.updateSpeed()
        t0 = time.time()
        if self.useHelperBot:
            return self.helperBot.choose_sense(sense_actions, move_actions, seconds_left)
        print('\nSensing now...')
        sense_move = select_sense(self.beliefState.myBoardDist, actuallyUs=True)
        print('\nSensing move is', sense_move)
        print(f"Chose a sensing action in {time.time()-t0} seconds.")
        return sense_move

    def handle_sense_result(self, sense_result: List[Tuple[Square, Optional[chess.Piece]]]):
        self.updateSpeed()

        phase, turn = Phase.SENSE_RESULT, self.turn
        # self.stash_and_add_history(phase, turn, (sense_result))
        self.stash.stash_boards(phase, turn, self.beliefState, self.maxInDist)
        self.stash.add_history(turn, phase, (sense_result))

        print('Updating belief state after sense result...')
        t0 = time.time()
        if self.useHelperBot:
            self.helperBot.handle_sense_result(sense_result)
            return
        try:
            self.beliefState.sense_update(sense_result, maxTime = self.handleSenseMaxTime)
        except EmptyBoardDist:
            self.get_new_boards()
        print('Our updated belief dist is now as follows:')
        self.beliefState.display(stash=self.stash)
        bestKey = max(self.beliefState.myBoardDist, key=self.beliefState.myBoardDist.get)
        print(bestKey, self.beliefState.myBoardDist[bestKey])
        print(f"Handled sense result in {time.time()-t0} seconds.")

    def choose_move(self, move_actions: List[chess.Move], seconds_left: float) -> Optional[chess.Move]:
        self.gameEndTime = time.time() + seconds_left
        self.updateSpeed()
        t0 = time.time()
        if self.useHelperBot:
            return self.helperBot.choose_move(move_actions, seconds_left)
        print(f"Choosing move with {self.gameEndTime - time.time()} seconds remaining...")
        move = select_move(self.beliefState, maxTime=self.chooseMoveMaxTime, useQuickMoveDist=self.useQuickMoveDist)
        print("MOVE:", move)
        if move == chess.Move.null():
            return None
        print(f"Chose a move in {time.time()-t0} seconds.")
        return move

    def handle_move_result(self, requested_move: Optional[chess.Move], taken_move: Optional[chess.Move],
                           captured_opponent_piece: bool, capture_square: Optional[Square]):
        phase, turn = Phase.OUR_MOVE_RESULT, self.turn
        # self.stash_and_add_history(phase, turn, (requested_move, taken_move, captured_opponent_piece, capture_square))
        self.stash.stash_boards(phase, turn, self.beliefState, self.maxInDist)
        self.stash.add_history(turn, phase, (requested_move, taken_move, captured_opponent_piece, capture_square))

        self.updateSpeed()
        t0 = time.time()
        print('Handling our move result:')
        print('\nRequested move', requested_move, ', took move', taken_move)
        if captured_opponent_piece: print(f"We captured a piece on {str(capture_square)}!")
        if self.useHelperBot:
            self.helperBot.handle_move_result(requested_move, taken_move, captured_opponent_piece, capture_square)
            return
        try:
            result = self.beliefState.our_move_result_update(requested_move, taken_move, captured_opponent_piece, capture_square, maxTime=self.handleMoveMaxTime)
            if result == "won":
                self.handle_game_end(self.color, WinReason.KING_CAPTURE, None)
                return
                # assert False, f"WE JUST WON AGAINST {self.opponent_name}"
        except EmptyBoardDist:
            self.get_new_boards()
        print(f"Handled our move result in {time.time()-t0} seconds.")
        
        t1 = time.time()
        print('\nAnticipating opponent sense...')
        try:
            self.beliefState.opp_sense_result_update()
        except EmptyBoardDist:
            self.get_new_boards()
        print(f"Handled anticipated opponent sensing action in {time.time()-t1} seconds.")
        self.turn += 1
        print("Waiting for opponent...")

    def handle_game_end(self, winner_color: Optional[Color], win_reason: Optional[WinReason],
                        game_history: GameHistory):
        if (game_history != None): game_history.save('games/game.json')
        # requests.post(f"{self.baseurl}/game_over/{self.gameId}")
        print(f"{'We' if winner_color == self.color else f'They ({self.opponent_name})'} beat {'us' if winner_color != self.color else self.opponent_name} by {win_reason}!")
        for engine_list in [moving_engines, [analysisEngine], extra_engines, [okayJustOneMore]]:
            for engine in engine_list:
                try:
                    engine.quit()
                except:
                    pass
